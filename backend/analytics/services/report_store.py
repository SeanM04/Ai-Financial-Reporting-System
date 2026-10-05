"""Persistent report storage backed by the database.

Reports are always read from the database. They are not cached in process memory,
because with several server processes a cached copy goes stale as soon as another
process saves the report.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from ..models import DataRetentionAuditLog, PersistedReport


def _resolve_owner(request) -> tuple[Any | None, str]:
    if request is None:
        return None, ''
    user = getattr(request, 'user', None)
    if user is not None and not isinstance(user, AnonymousUser) and user.is_authenticated:
        return user, user.username
    return None, ''


def visible_reports_q(user) -> Q:
    """Reports a user may see: their own; staff also see reports with no owner."""
    if user.is_staff:
        return Q(owner=user) | Q(owner__isnull=True)
    return Q(owner=user)


def user_can_access_report(user, report_id: str) -> bool:
    """Owners can open their reports. Staff can open any report, including unowned ones."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    try:
        return PersistedReport.objects.filter(pk=str(report_id), owner=user).exists()
    except (ValueError, ValidationError):
        return False


def list_report_ids() -> list[str]:
    return [str(report_id) for report_id in PersistedReport.objects.values_list('id', flat=True)]


def list_reports(request=None) -> list[dict[str, Any]]:
    user, _ = _resolve_owner(request)
    if user is None:
        return []
    queryset = PersistedReport.objects.filter(visible_reports_q(user))
    return [record.report_data for record in queryset]


def get_report(report_id: str) -> dict[str, Any] | None:
    try:
        record = PersistedReport.objects.get(pk=str(report_id))
    except (PersistedReport.DoesNotExist, ValueError):
        return None
    return record.report_data


def get_report_record(report_id: str) -> PersistedReport | None:
    """Return the database row for a persisted report, if it exists."""
    try:
        return PersistedReport.objects.get(pk=str(report_id))
    except (PersistedReport.DoesNotExist, ValueError):
        return None


def save_report(report_id: str, report_data: dict[str, Any], request=None) -> dict[str, Any]:
    report_id = str(report_id)
    report_data = dict(report_data)
    report_data['id'] = report_id

    owner, owner_username = _resolve_owner(request)
    defaults = {'report_data': report_data}
    # Never clear an existing owner when a save happens without a logged-in user.
    if owner is not None:
        defaults.update({'owner': owner, 'owner_username': owner_username})
    PersistedReport.objects.update_or_create(id=uuid.UUID(report_id), defaults=defaults)
    return report_data


def update_report(report_id: str, updates: dict[str, Any], request=None) -> dict[str, Any] | None:
    report = get_report(report_id)
    if not report:
        return None

    report.update(updates)
    return save_report(str(report_id), report, request=request)


def list_report_records(
    request=None,
    *,
    include_archived: bool = True,
    search: str = '',
    status: str = '',
) -> list[PersistedReport]:
    user, _ = _resolve_owner(request)
    if user is None:
        return []
    queryset = PersistedReport.objects.filter(visible_reports_q(user))

    if not include_archived:
        queryset = queryset.filter(is_archived=False)

    if search:
        search_lower = search.lower()
        queryset = [
            record for record in queryset
            if search_lower in str(record.report_data.get('filename', '')).lower()
            or search_lower in str(record.report_data.get('bank_name', '')).lower()
            or search_lower in str(record.report_data.get('metadata', {}).get('title', '')).lower()
        ]
        if status:
            queryset = [record for record in queryset if str(record.report_data.get('status', '')).lower() == status.lower()]
        return list(queryset)

    records = list(queryset.order_by('-created_at'))
    if status:
        records = [record for record in records if str(record.report_data.get('status', '')).lower() == status.lower()]
    return records


def archive_reports(report_ids: list[str], request=None) -> dict[str, Any]:
    ids = [uuid.UUID(str(report_id)) for report_id in report_ids]
    queryset = PersistedReport.objects.filter(id__in=ids)
    updated = queryset.update(is_archived=True, archived_at=timezone.now())
    _log_retention_action(request, 'archive', report_ids)
    return {'updated_count': updated}


def restore_reports(report_ids: list[str], request=None) -> dict[str, Any]:
    ids = [uuid.UUID(str(report_id)) for report_id in report_ids]
    queryset = PersistedReport.objects.filter(id__in=ids)
    updated = queryset.update(is_archived=False, archived_at=None)
    _log_retention_action(request, 'restore', report_ids)
    return {'updated_count': updated}


def delete_reports(report_ids: list[str], request=None) -> dict[str, Any]:
    ids = [uuid.UUID(str(report_id)) for report_id in report_ids]
    queryset = PersistedReport.objects.filter(id__in=ids)
    deleted_count, _ = queryset.delete()
    _log_retention_action(request, 'delete', report_ids)
    return {'deleted_count': deleted_count}


def _log_retention_action(request, action: str, report_ids: list[str], metadata: dict[str, Any] | None = None) -> None:
    user, _ = _resolve_owner(request)
    DataRetentionAuditLog.objects.create(
        user=user,
        action=action,
        report_ids=[str(report_id) for report_id in report_ids],
        metadata=metadata or {},
    )
