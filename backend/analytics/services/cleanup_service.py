"""
Service for cleaning up old data based on user retention settings.
"""

from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from ..models import UserSettings, PersistedReport, FinancialDataUpload, DataRetentionAuditLog


def get_user_retention_settings(user):
    """
    Get retention settings for a user.
    
    Returns:
        dict: {
            'retention_days': int,
            'retention_unit': str ('days', 'weeks', 'months'),
            'auto_cleanup': bool
        }
    """
    try:
        user_settings = UserSettings.objects.get(user=user)
        settings = user_settings.settings or {}
        
        retention_days = max(int(settings.get('retentionDays', 90) or 90), 1)
        retention_unit = settings.get('retentionUnit', 'days')
        auto_cleanup = settings.get('autoCleanup', False)
        
        return {
            'retention_days': retention_days,
            'retention_unit': retention_unit,
            'auto_cleanup': auto_cleanup
        }
    except UserSettings.DoesNotExist:
        return {
            'retention_days': 90,
            'retention_unit': 'days',
            'auto_cleanup': False
        }


def convert_retention_to_days(retention_days, retention_unit):
    """
    Convert retention value and unit to days.
    
    Args:
        retention_days: int
        retention_unit: str ('days', 'weeks', 'months')
    
    Returns:
        int: Number of days
    """
    if retention_unit == 'weeks':
        return retention_days * 7
    elif retention_unit == 'months':
        return retention_days * 30  # Approximate
    else:  # 'days' or default
        return retention_days


def cleanup_old_reports(user=None, dry_run=False):
    """
    Delete reports older than the user's retention period.
    
    Args:
        user: User object. If None, applies global cleanup for all users.
        dry_run: bool. If True, only return count without deleting.
    
    Returns:
        dict: {
            'deleted_count': int,
            'report_ids': list,
            'dry_run': bool
        }
    """
    deleted_count = 0
    deleted_ids = []
    
    if user:
        # User-specific cleanup
        settings = get_user_retention_settings(user)
        
        if not settings['auto_cleanup']:
            return {
                'deleted_count': 0,
                'report_ids': [],
                'dry_run': dry_run,
                'status': 'skipped_auto_cleanup_disabled'
            }
        
        retention_days = convert_retention_to_days(
            settings['retention_days'],
            settings['retention_unit']
        )
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        # Get reports older than retention period
        # Regular users only clean up their own reports; staff also clear unowned ones.
        owner_q = Q(owner=user) | Q(owner__isnull=True) if user.is_staff else Q(owner=user)
        old_reports = PersistedReport.objects.filter(
            owner_q,
            created_at__lt=cutoff_date,
            is_archived=False,
        )
        old_reports = old_reports.exclude(report_data__status='processing')
        
        deleted_ids = list(old_reports.values_list('id', flat=True))
        deleted_count = old_reports.count()
        
        if not dry_run and deleted_count > 0:
            old_reports.delete()
            DataRetentionAuditLog.objects.create(
                user=user,
                action='cleanup',
                report_ids=[str(id) for id in deleted_ids],
                metadata={
                    'retention_days': retention_days,
                    'retention_unit': settings['retention_unit'],
                    'scope': 'reports',
                },
            )
        
        return {
            'deleted_count': deleted_count,
            'report_ids': [str(id) for id in deleted_ids],
            'dry_run': dry_run,
            'retention_days': retention_days,
            'cutoff_date': cutoff_date.isoformat()
        }
    
    else:
        # Global cleanup for all users with auto-cleanup enabled
        from django.contrib.auth.models import User
        
        total_deleted = 0
        total_ids = []
        
        for u in User.objects.all():
            result = cleanup_old_reports(user=u, dry_run=dry_run)
            if result.get('deleted_count', 0) > 0:
                total_deleted += result['deleted_count']
                total_ids.extend(result.get('report_ids', []))
        
        return {
            'deleted_count': total_deleted,
            'report_ids': total_ids,
            'dry_run': dry_run,
            'users_processed': User.objects.count()
        }


def cleanup_old_uploads(user=None, dry_run=False):
    """
    Delete financial data uploads older than the user's retention period.
    
    Args:
        user: User object. If None, applies global cleanup for all users.
        dry_run: bool. If True, only return count without deleting.
    
    Returns:
        dict: {
            'deleted_count': int,
            'upload_ids': list,
            'dry_run': bool
        }
    """
    deleted_count = 0
    deleted_ids = []
    
    if user:
        # User-specific cleanup
        settings = get_user_retention_settings(user)
        
        if not settings['auto_cleanup']:
            return {
                'deleted_count': 0,
                'upload_ids': [],
                'dry_run': dry_run,
                'status': 'skipped_auto_cleanup_disabled'
            }
        
        retention_days = convert_retention_to_days(
            settings['retention_days'],
            settings['retention_unit']
        )
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        # Get uploads older than retention period
        old_uploads = FinancialDataUpload.objects.filter(
            user=user,
            uploaded_at__lt=cutoff_date
        )
        
        deleted_ids = list(old_uploads.values_list('id', flat=True))
        deleted_count = old_uploads.count()
        
        if not dry_run and deleted_count > 0:
            old_uploads.delete()  # Cascade will delete related FinancialDataSet, etc.
            DataRetentionAuditLog.objects.create(
                user=user,
                action='cleanup',
                report_ids=[str(id) for id in deleted_ids],
                metadata={
                    'retention_days': retention_days,
                    'retention_unit': settings['retention_unit'],
                    'scope': 'uploads',
                },
            )
        
        return {
            'deleted_count': deleted_count,
            'upload_ids': [str(id) for id in deleted_ids],
            'dry_run': dry_run,
            'retention_days': retention_days,
            'cutoff_date': cutoff_date.isoformat()
        }
    
    else:
        # Global cleanup for all users with auto-cleanup enabled
        from django.contrib.auth.models import User
        
        total_deleted = 0
        total_ids = []
        
        for u in User.objects.all():
            result = cleanup_old_uploads(user=u, dry_run=dry_run)
            if result.get('deleted_count', 0) > 0:
                total_deleted += result['deleted_count']
                total_ids.extend(result.get('upload_ids', []))
        
        return {
            'deleted_count': total_deleted,
            'upload_ids': total_ids,
            'dry_run': dry_run,
            'users_processed': User.objects.count()
        }


def cleanup_all_old_data(user=None, dry_run=False):
    """
    Run cleanup for both reports and uploads.
    
    Args:
        user: User object. If None, applies global cleanup.
        dry_run: bool. If True, only return count without deleting.
    
    Returns:
        dict: Combined cleanup results
    """
    reports_result = cleanup_old_reports(user=user, dry_run=dry_run)
    uploads_result = cleanup_old_uploads(user=user, dry_run=dry_run)
    
    return {
        'reports_deleted': reports_result['deleted_count'],
        'uploads_deleted': uploads_result['deleted_count'],
        'total_deleted': reports_result['deleted_count'] + uploads_result['deleted_count'],
        'reports': reports_result,
        'uploads': uploads_result,
        'dry_run': dry_run
    }
