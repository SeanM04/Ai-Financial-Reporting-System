"""Report and insights endpoints."""

import json
import uuid
import time
import re
from datetime import datetime

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.core.cache import cache
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from django.contrib.auth import get_user_model

from .access import ai_rate_limited, api_login_required, report_access_required

from ..models import UserSettings

from ..services.report_store import (
    archive_reports,
    delete_reports,
    get_report,
    get_report_record,
    list_report_ids,
    list_report_records,
    list_reports,
    restore_reports,
    update_report,
)
from ..services.report_section_store import append_section_version, get_section_history
from ..services.report_prompt_registry import clear_report_prompt_registry_cache, get_report_prompt_registry
from ..services.prompt_settings_store import (
    build_prompt_config_payload,
    get_analysis_prompt,
    list_analysis_prompts,
    reset_all_analysis_prompts,
    reset_analysis_prompt,
    save_report_configuration,
    serialize_analysis_prompt,
    update_analysis_prompt_content,
    ensure_prompt_defaults,
)
from ..views import (
    BenchmarkComparisonView,
    CustomReportView,
    ExportReportView,
    FinancialReportViewSet,
    MetricsSummaryView,
    ReportDetailView,
    TrendAnalysisView,
    get_insights,
    regenerate_insights,
    count_report_words,
    generate_analysis_from_prompt,
    generate_csv_report,
    generate_excel_report,
    generate_pdf_report,
    generate_report_sections,
    generate_word_report,
)


@require_http_methods(["GET"])
@api_login_required
def simple_reports_view(request):
    """Return the reports the current user may see."""
    reports = list_reports(request)
    return JsonResponse({
        'results': reports,
        'count': len(reports),
    })


@require_http_methods(["GET"])
@report_access_required
def simple_report_detail_view(request, report_id):
    """Return a single report from the shared report store."""
    report = get_report(str(report_id))
    if not report:
        return JsonResponse({
            'error': 'Report not found',
            'report_id': str(report_id),
            'message': 'No matching report is available. Please upload a financial data file first.',
        }, status=404)

    payload = dict(report)
    try:
        from ..services.prompt_module_store import get_report_section_mappings
        payload['section_mappings'] = get_report_section_mappings(str(report_id))
    except Exception:
        payload['section_mappings'] = []
    return JsonResponse(payload)


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@report_access_required
def report_section_mappings_view(request, report_id):
    from ..services.prompt_module_store import get_report_section_mappings

    if not get_report(str(report_id)):
        return JsonResponse({'error': 'Report not found'}, status=404)
    mappings = get_report_section_mappings(str(report_id))
    return JsonResponse({'report_id': str(report_id), 'section_mappings': mappings, 'count': len(mappings)})


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@report_access_required
@ai_rate_limited
def regenerate_report_section_view(request, report_id, section_key):
    """Regenerate only one report section after validating and persisting its prompt."""
    from django.conf import settings
    from ..services.prompt_module_store import (
        apply_section_traceability,
        get_prompt_module_for_section,
        serialize_prompt_module_version,
        update_prompt_module,
    )
    from ..views import (
        build_data_summary,
        extract_entity_metadata,
        generate_comprehensive_ai_analysis,
        normalize_json_for_analysis,
        perform_initial_ai_analysis,
    )

    report_id = str(report_id)
    section_key = str(section_key).strip()
    request_id = uuid.uuid4().hex
    lock_key = f'analytics:regen:{report_id}:{section_key}'
    body = request.data if isinstance(getattr(request, 'data', None), dict) else {}
    edited_prompt = str(body.get('prompt') or '').strip()
    reason = str(body.get('reason') or 'section regeneration').strip() or 'section regeneration'

    if not section_key:
        return JsonResponse({'error': 'Invalid section key.'}, status=400)
    if not edited_prompt:
        return JsonResponse({'error': 'Prompt cannot be empty.'}, status=400)
    if len(edited_prompt) < 3:
        return JsonResponse({'error': 'Prompt is too short.'}, status=400)
    if len(edited_prompt) > 12000:
        return JsonResponse({'error': 'Prompt exceeds maximum allowed length.'}, status=400)

    report = get_report(report_id)
    if not report:
        return JsonResponse({'error': 'Report not found'}, status=404)

    record = get_report_record(report_id)
    if record and record.owner_id and record.owner_id != request.user.id and not request.user.is_staff:
        return JsonResponse({'error': 'You do not have access to this report.'}, status=403)

    original_json = (report.get('metadata') or {}).get('original_json')
    if not original_json:
        return JsonResponse({'error': 'Original JSON data not found in report'}, status=400)

    sections = list(report.get('comprehensive_analysis') or [])
    expected_section_keys = [
        str(k).strip()
        for k in (
            (report.get('report_options') or {}).get('sections')
            or (report.get('metadata') or {}).get('report_options', {}).get('sections')
            or []
        )
        if str(k).strip()
    ]
    section_index = None
    section_key_match = None
    if section_key.startswith('section_'):
        try:
            section_index = int(section_key.split('_', 1)[1])
        except (IndexError, ValueError):
            section_index = None
    existing_section = None
    for idx, section in enumerate(sections):
        key = str(section.get('section_key') or section.get('key') or '').strip()
        title_key = str(section.get('title', '')).strip().lower().replace(' ', '_')
        if key == section_key or title_key == section_key:
            existing_section = section
            section_key_match = key or title_key or f'section_{idx}'
            break
        if section_index is not None and idx == section_index:
            existing_section = section
            section_key_match = key or title_key or f'section_{idx}'
            break

    # Allow generating a section that was expected but missing from AI output.
    if not existing_section and section_key in expected_section_keys:
        section_key_match = section_key
        existing_section = {
            'section_key': section_key,
            'title': section_key.replace('_', ' ').title(),
            'content': {},
        }

    if not existing_section:
        return JsonResponse({'error': 'Invalid section key for this report.'}, status=400)

    resolved_section_key = section_key_match or section_key

    section_history = dict(report.get('section_history') or {})
    prior_history = list(section_history.get(resolved_section_key) or [])

    report_options = dict(report.get('report_options') or (report.get('metadata') or {}).get('report_options') or {})
    # Force a single-section regeneration context so the AI does not rebuild the full report.
    report_options['sections'] = [resolved_section_key]
    report_options['include_sections'] = [resolved_section_key]
    report_options['exclude_sections'] = [
        key for key in (report.get('report_options') or {}).get('sections', [])
        if key != resolved_section_key
    ]
    report_options['template'] = 'custom'
    report_options['template_type'] = 'custom'

    section_prompt_module = None
    if not re.match(r'^section_\d+$', section_key):
        section_prompt_module = get_prompt_module_for_section(section_key)
    exact_module = None
    if section_prompt_module:
        related_sections = [str(item).strip() for item in (section_prompt_module.related_sections or [])]
        if section_key in related_sections or section_prompt_module.slug in {section_key, section_key.replace('_', '-')}:
            exact_module = section_prompt_module

    if not cache.add(lock_key, request_id, timeout=15 * 60):
        return JsonResponse({'error': 'Another regeneration is already running for this section.'}, status=409)

    try:
        bank_name, data_period = extract_entity_metadata(original_json)
        normalized_json, _ = normalize_json_for_analysis(original_json)

        module = exact_module
        if module is not None:
            module = update_prompt_module(
                module,
                {'prompt_text': edited_prompt, 'status': 'active'},
                user=request.user,
                change_comment=reason,
            )

        prompt_version_obj = module.versions.order_by('-version_number', '-created_at').first() if module else None
        prompt_version = serialize_prompt_module_version(prompt_version_obj) if prompt_version_obj else {
            'id': None,
            'version_number': 1,
            'prompt_text': edited_prompt,
        }

        generation_started = time.perf_counter()
        analysis_result = generate_comprehensive_ai_analysis({
            'bank_name': bank_name or report.get('bank_name'),
            'data_period': data_period or report.get('data_period'),
            'financial_data': normalized_json,
            'raw_financial_data': original_json,
            'existing_analysis': report.get('ai_analysis') or perform_initial_ai_analysis(original_json),
            'data_summary': build_data_summary(original_json),
            # Use only the edited section prompt — not the full multi-section master —
            # so the model returns a single section.
            'user_prompt': edited_prompt,
            'report_options': report_options,
            'section_prompt_overrides': {resolved_section_key: edited_prompt},
        })
        generation_duration_ms = int((time.perf_counter() - generation_started) * 1000)

        # A rule-based fallback is not a regeneration; keep the existing section.
        if not analysis_result or not analysis_result.get('success') or not analysis_result.get('ai_enhanced'):
            return JsonResponse({'error': (analysis_result or {}).get('error') or 'Section regeneration failed'}, status=503)

        new_sections = analysis_result.get('sections') or []
        if not new_sections:
            return JsonResponse({'error': 'The AI returned an invalid section structure.'}, status=500)

        chosen = None
        for candidate in new_sections:
            cand_key = str(candidate.get('section_key') or candidate.get('key') or '').strip()
            cand_title = str(candidate.get('title', '')).strip().lower().replace(' ', '_')
            if cand_key == resolved_section_key or cand_title == resolved_section_key:
                chosen = candidate
                break
        if chosen is None:
            chosen = new_sections[0]

        new_section = dict(chosen)
        new_section['section_key'] = resolved_section_key
        if not new_section.get('content'):
            return JsonResponse({'error': 'The AI returned an empty section.'}, status=500)

        previous = dict(existing_section)
        prior_history.append({
            'content': existing_section.get('content'),
            'title': existing_section.get('title'),
            'trace': existing_section.get('trace'),
            'replaced_at': datetime.now().isoformat(),
        })
        section_history[resolved_section_key] = prior_history[-10:]

        existing = []
        replaced = False
        for section in sections:
            key = str(section.get('section_key') or section.get('key') or '').strip()
            title = str(section.get('title', '')).strip().lower().replace(' ', '_')
            if not replaced and (key == section_key or key == resolved_section_key or title == section_key or title == resolved_section_key):
                existing.append(new_section)
                replaced = True
            else:
                existing.append(section)
        # If no existing section matched, append the new section at the end.
        if not replaced:
            existing.append(new_section)

        model_used = analysis_result.get('model_used') or getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')
        usage = analysis_result.get('usage') or {}
        annotated = apply_section_traceability(
            report_id,
            existing,
            report_options={**report_options, 'sections': [s.get('section_key') or resolved_section_key for s in existing]},
            ai_model=model_used,
            confidence_score=0.85,
            regeneration_reason=reason,
        )

        # Ensure section keys are unique to avoid duplicate entries displayed
        # in the frontend (which can cause left/right pane mismatches).
        seen_keys = set()
        unique_annotated = []
        for s in annotated or []:
            k = str(s.get('section_key') or s.get('key') or '').strip()
            if not k:
                unique_annotated.append(s)
                continue
            if k in seen_keys:
                # skip duplicate
                continue
            seen_keys.add(k)
            unique_annotated.append(s)
        annotated = unique_annotated

        try:
            from ..services.prompt_module_store import compose_master_prompt

            all_section_keys = (
                (report.get('report_options') or {}).get('sections')
                or (report.get('metadata') or {}).get('report_options', {}).get('sections')
                or [section_key]
            )
            master_prompt = compose_master_prompt(
                all_section_keys,
                {'bank_name': bank_name, 'data_period': data_period},
            )
            
            # Update the Dataset Analysis Prompt if this was a dataset-based report
            dataset_type = report.get('dataset_type') or report.get('metadata', {}).get('dataset_type')
            if dataset_type:
                from ..api.uploads import DATASET_TYPE_RULES
                if dataset_type in DATASET_TYPE_RULES:
                    dataset_rule = DATASET_TYPE_RULES[dataset_type]
                    prompt_id = dataset_rule.get('prompt_id')
                    if prompt_id:
                        try:
                            update_analysis_prompt_content(
                                prompt_id,
                                master_prompt,
                                user=request.user,
                            )
                        except Exception as prompt_update_error:
                            # Log but don't fail the section regeneration if prompt update fails
                            import logging
                            logging.getLogger(__name__).warning(
                                f'Failed to update Dataset Analysis Prompt {prompt_id}: {prompt_update_error}'
                            )
        except Exception:
            master_prompt = report.get('user_prompt') or ''

        new_metadata = dict(report.get('metadata') or {})
        new_metadata['user_prompt'] = master_prompt
        new_metadata['master_prompt_updated_at'] = datetime.now().isoformat()
        new_metadata['master_prompt_updated_by'] = request.user.username if getattr(request.user, 'is_authenticated', False) else None

        with transaction.atomic():
            update_report(report_id, {
                'comprehensive_analysis': annotated,
                'section_history': section_history,
                'ai_enhanced': True,
                'user_prompt': master_prompt,
                'metadata': new_metadata,
                'generation_request_id': request_id,
            }, request=request)

            version = append_section_version(
                report_id,
                section_key,
                section=new_section,
                prompt_version=prompt_version,
                model_used=model_used,
                generation_status='SUCCESS',
                generation_reason=reason,
                generated_by=request.user.username if getattr(request.user, 'is_authenticated', False) else None,
                request_id=request_id,
                duration_ms=generation_duration_ms,
                usage=usage,
            )

        regenerated = next((s for s in annotated if s.get('section_key') == section_key), annotated[-1] if annotated else new_section)
        return JsonResponse({
            'success': True,
            'report_id': report_id,
                'section_key': section_key,
                'resolved_section_key': resolved_section_key,
            'section': regenerated,
            'previous_section': previous,
            'prompt_module': {
                'id': module.id,
                'name': module.name,
                'version_current': module.version_current,
            } if module else None,
            'prompt_version': prompt_version,
            'section_version': version,
            'request_id': request_id,
            'usage': usage,
            'duration_ms': generation_duration_ms,
        })
    finally:
        cache.delete(lock_key)


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@report_access_required
def save_master_prompt_to_dataset_prompt(request, report_id):
    """Save master prompt edits back to the Dataset Analysis Prompt."""
    try:
        report = get_report(str(report_id))
        if not report:
            return JsonResponse({'error': 'Report not found'}, status=404)

        record = get_report_record(report_id)
        if record and record.owner_id and record.owner_id != request.user.id and not request.user.is_staff:
            return JsonResponse({'error': 'You do not have access to this report.'}, status=403)

        body = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        master_prompt = str(body.get('master_prompt') or '').strip()

        if not master_prompt:
            return JsonResponse({'error': 'Master prompt cannot be empty.'}, status=400)

        # Get the dataset type from the report
        dataset_type = report.get('dataset_type') or report.get('metadata', {}).get('dataset_type')
        if not dataset_type:
            return JsonResponse({'error': 'No dataset type found in report.'}, status=400)

        from ..api.uploads import DATASET_TYPE_RULES
        if dataset_type not in DATASET_TYPE_RULES:
            return JsonResponse({'error': f'Invalid dataset type: {dataset_type}'}, status=400)

        dataset_rule = DATASET_TYPE_RULES[dataset_type]
        prompt_id = dataset_rule.get('prompt_id')
        if not prompt_id:
            return JsonResponse({'error': 'No prompt ID found for dataset type.'}, status=400)

        # Update the Dataset Analysis Prompt
        updated_prompt = update_analysis_prompt_content(
            prompt_id,
            master_prompt,
            user=request.user,
        )

        # Also update the report's master prompt
        new_metadata = dict(report.get('metadata') or {})
        new_metadata['user_prompt'] = master_prompt
        new_metadata['master_prompt_updated_at'] = datetime.now().isoformat()
        new_metadata['master_prompt_updated_by'] = request.user.username if getattr(request.user, 'is_authenticated', False) else None

        update_report(report_id, {
            'user_prompt': master_prompt,
            'metadata': new_metadata,
        }, request=request)

        return JsonResponse({
            'success': True,
            'prompt_id': prompt_id,
            'updated_at': updated_prompt.updated_at.isoformat() if updated_prompt.updated_at else None,
            'message': f'Successfully updated Dataset Analysis Prompt for {dataset_type.upper()}'
        })

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f'Error saving master prompt to dataset prompt: {e}')
        return JsonResponse({'error': f'Failed to save master prompt: {str(e)}'}, status=500)


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@report_access_required
def report_section_history_view(request, report_id, section_key):
    report = get_report(str(report_id))
    if not report:
        return JsonResponse({'error': 'Report not found'}, status=404)
    history = get_section_history(str(report_id), str(section_key))
    if not history and str(section_key).startswith('section_'):
        try:
            idx = int(str(section_key).split('_', 1)[1])
        except (IndexError, ValueError):
            idx = None
        if idx is not None:
            sections = list(report.get('comprehensive_analysis') or [])
            if 0 <= idx < len(sections):
                resolved_key = str(sections[idx].get('section_key') or sections[idx].get('key') or section_key)
                history = get_section_history(str(report_id), resolved_key)
    return JsonResponse({
        'success': True,
        'report_id': str(report_id),
        'section_key': str(section_key),
        'history': history,
        'count': len(history),
    })


def _resolve_export_format(request):
    """Read export format from query string, form body, or JSON body."""
    format_type = request.GET.get('format')
    if format_type:
        return format_type

    if request.POST.get('format'):
        return request.POST.get('format')

    try:
        body = json.loads(request.body or b'{}')
        if isinstance(body, dict) and body.get('format'):
            return body.get('format')
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    return 'json'


def _normalize_export_format(format_type):
    """Map URL aliases to canonical export format names."""
    if not format_type:
        return 'json'

    normalized = str(format_type).lower().strip()
    aliases = {
        'docx': 'word',
        'doc': 'word',
        'xlsx': 'excel',
        'print': 'pdf',
        'editable': 'word',
    }
    return aliases.get(normalized, normalized)


@require_http_methods(["GET", "POST"])
@report_access_required
def simple_export_view(request, report_id, file_type=None):
    """Download a stored report in the requested format."""
    report = get_report(str(report_id))
    if not report:
        return JsonResponse({
            'error': 'Report not found',
            'report_id': str(report_id),
            'available_ids': list_report_ids(),
        }, status=404)

    export_data = {
        'report_id': report.get('id'),
        'filename': report.get('filename'),
        'bank_name': report.get('bank_name'),
        'data_period': report.get('data_period'),
        'generated_at': report.get('uploaded_at') or report.get('metadata', {}).get('generated_at'),
        'user_prompt': report.get('user_prompt') or report.get('metadata', {}).get('user_prompt'),
        'ai_enhanced': report.get('ai_enhanced', False),
        'data_summary': report.get('data_summary'),
        'ai_analysis': report.get('ai_analysis'),
        'comprehensive_analysis': report.get('comprehensive_analysis', []),
        'metadata': report.get('metadata'),
        'export_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    format_type = _normalize_export_format(file_type or _resolve_export_format(request))

    if format_type == 'json':
        response = JsonResponse(export_data)
        response['Content-Disposition'] = f'attachment; filename="financial_report_{report_id}.json"'
        response['Content-Type'] = 'application/json'
        return response

    if format_type == 'pdf':
        content = generate_pdf_report(report)
        response = HttpResponse(content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="financial_report_{report_id}.pdf"'
        return response

    if format_type == 'csv':
        content = generate_csv_report(report)
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="financial_report_{report_id}.csv"'
        return response

    if format_type == 'word':
        content = generate_word_report(report)
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        response['Content-Disposition'] = f'attachment; filename="financial_report_{report_id}.docx"'
        return response

    if format_type == 'excel':
        content = generate_excel_report(report)
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="financial_report_{report_id}.xlsx"'
        return response

    return JsonResponse({'error': f'Unsupported format: {format_type}'}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_report_templates(request):
    """Get available report templates."""
    registry = get_report_prompt_registry()
    return JsonResponse({
        'success': True,
        'templates': registry.get_templates(),
        'section_library': registry.get_section_library(),
    })


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_report_prompt_config(request):
    """Return the editable prompt configuration."""
    ensure_prompt_defaults()
    config = build_prompt_config_payload()
    prompts = [serialize_analysis_prompt(prompt) for prompt in list_analysis_prompts()]
    return JsonResponse({
        'success': True,
        'config': config,
        'prompts': prompts,
        'is_admin': bool(request.user.is_staff or request.user.is_superuser),
    })


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def update_report_prompt_config(request):
    """Persist report prompt configuration updates."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({'error': 'Configuration payload must be a JSON object'}, status=400)

    config = save_report_configuration(body, user=request.user)
    clear_report_prompt_registry_cache()
    prompts = [serialize_analysis_prompt(prompt) for prompt in list_analysis_prompts()]
    return JsonResponse({
        'success': True,
        'config': config,
        'prompts': prompts,
    })


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_analysis_prompts(request):
    """Return persisted AI analysis prompts for the Upload page."""
    ensure_prompt_defaults()
    return JsonResponse({
        'success': True,
        'is_admin': bool(request.user.is_staff or request.user.is_superuser),
        'prompts': [serialize_analysis_prompt(prompt) for prompt in list_analysis_prompts()],
    })


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def update_analysis_prompt_view(request):
    """Update a single analysis prompt in the database."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    prompt_id = body.get('prompt_id')
    content = body.get('content', '')
    if not prompt_id:
        return JsonResponse({'error': 'prompt_id is required'}, status=400)
    if not str(content).strip():
        return JsonResponse({'error': 'Prompt content cannot be empty'}, status=400)

    try:
        prompt = update_analysis_prompt_content(prompt_id, str(content), user=request.user)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=404)

    clear_report_prompt_registry_cache()
    return JsonResponse({
        'success': True,
        'prompt': serialize_analysis_prompt(prompt),
    })


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def reset_analysis_prompts_view(request):
    """Reset one or all analysis prompts to their built-in defaults."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    prompt_id = body.get('prompt_id', 'all')
    try:
        if prompt_id == 'all':
            prompts = reset_all_analysis_prompts(user=request.user)
        else:
            prompts = [reset_analysis_prompt(prompt_id, user=request.user)]
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=404)

    clear_report_prompt_registry_cache()
    return JsonResponse({
        'success': True,
        'prompts': [serialize_analysis_prompt(prompt) for prompt in prompts],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_settings(request):
    """Return persisted settings for the authenticated user."""
    user = request.user
    try:
        us = UserSettings.objects.get(user=user)
        return JsonResponse({'success': True, 'settings': us.settings})
    except UserSettings.DoesNotExist:
        return JsonResponse({'success': True, 'settings': {} })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def update_user_settings(request):
    """Persist settings for the authenticated user."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({'error': 'Settings payload must be a JSON object'}, status=400)

    retention_days = body.get('retentionDays')
    retention_unit = body.get('retentionUnit')
    if retention_days is not None:
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'retentionDays must be an integer'}, status=400)
        if retention_days < 1:
            return JsonResponse({'error': 'retentionDays must be greater than 0'}, status=400)
        body['retentionDays'] = retention_days

    if retention_unit is not None and retention_unit not in {'days', 'weeks', 'months'}:
        return JsonResponse({'error': 'retentionUnit must be days, weeks, or months'}, status=400)

    user = request.user
    us, _ = UserSettings.objects.get_or_create(user=user)
    # accept nested structure; overwrite stored settings
    us.settings = body
    us.save()
    return JsonResponse({'success': True, 'settings': us.settings})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def list_manageable_reports(request):
    """List reports for management table with filters."""
    search = request.query_params.get('search', '').strip()
    status = request.query_params.get('status', '').strip()
    include_archived = request.query_params.get('include_archived', 'true').lower() == 'true'
    records = list_report_records(
        request=request,
        include_archived=include_archived,
        search=search,
        status=status,
    )
    items = []
    for record in records:
        report = record.report_data or {}
        items.append({
            'id': str(record.id),
            'filename': report.get('filename'),
            'bank_name': report.get('bank_name'),
            'status': report.get('status', 'completed'),
            'ai_enhanced': report.get('ai_enhanced', False),
            'data_period': report.get('data_period'),
            'created_at': record.created_at.isoformat(),
            'updated_at': record.updated_at.isoformat(),
            'is_archived': record.is_archived,
            'archived_at': record.archived_at.isoformat() if record.archived_at else None,
            'title': report.get('metadata', {}).get('title') or report.get('filename') or str(record.id),
            'report_type': report.get('report_type') or report.get('metadata', {}).get('template_name') or 'analysis',
        })
    return JsonResponse({'success': True, 'results': items, 'count': len(items)})


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def bulk_report_action(request):
    """Archive, restore, or delete reports in bulk."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    action = str(body.get('action', '')).strip().lower()
    ids = body.get('report_ids') or []
    if action not in {'archive', 'restore', 'delete'}:
        return JsonResponse({'error': 'action must be one of: archive, restore, delete'}, status=400)
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'report_ids must be a non-empty list'}, status=400)

    try:
        if action == 'archive':
            result = archive_reports(ids, request=request)
        elif action == 'restore':
            result = restore_reports(ids, request=request)
        else:
            result = delete_reports(ids, request=request)
    except ValueError:
        return JsonResponse({'error': 'Invalid report id in report_ids'}, status=400)

    return JsonResponse({'success': True, 'action': action, 'result': result})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_data_cleanup(request):
    """
    Trigger cleanup of old reports and uploads for the authenticated user.
    
    Query Parameters:
        - dry_run: bool (default: True). If true, preview without deleting.
    
    Returns:
        Cleanup results including number of items deleted.
    """
    try:
        from analytics.services.cleanup_service import cleanup_all_old_data
        
        dry_run = request.query_params.get('dry_run', 'true').lower() == 'true'
        
        result = cleanup_all_old_data(user=request.user, dry_run=dry_run)
        
        return JsonResponse({
            'success': True,
            'dry_run': dry_run,
            'result': result,
            'message': f"Cleanup completed: {result['total_deleted']} items processed"
        })
    
    except Exception as e:
        return JsonResponse(
            {'error': str(e), 'success': False},
            status=400
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def preview_cleanup(request):
    """
    Preview what would be deleted based on user's retention settings.
    
    Returns:
        Dry-run cleanup results showing what would be deleted.
    """
    try:
        from analytics.services.cleanup_service import cleanup_all_old_data
        
        result = cleanup_all_old_data(user=request.user, dry_run=True)
        
        return JsonResponse({
            'success': True,
            'preview': True,
            'result': result,
            'message': f"{result['total_deleted']} items would be deleted"
        })
    
    except Exception as e:
        return JsonResponse(
            {'error': str(e), 'success': False},
            status=400
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@report_access_required
@ai_rate_limited
def generate_comprehensive_report(request, report_id):
    """Generate comprehensive financial report."""
    try:
        body = json.loads(request.body) if request.method == 'POST' else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    registry = get_report_prompt_registry()
    report_options = registry.build_report_options({
        'template': body.get('template', 'standard'),
        'sections': body.get('sections') or body.get('selected_sections') or [],
        'include_sections': body.get('include_sections') or [],
        'exclude_sections': body.get('exclude_sections') or [],
        'length': body.get('length'),
        'detail_level': body.get('detail_level'),
        'output_format': body.get('format', 'json'),
    })
    template_type = report_options.get('template', 'custom')
    format_type = report_options.get('output_format', 'json')
    report = get_report(str(report_id))

    if not report:
        return JsonResponse({'error': 'Report not found'}, status=404)

    templates = registry.get_templates()
    template = templates.get(template_type, templates.get('custom', {}))
    sections = report_options['sections'] or template.get('sections', [])
    generated_report = {
        'report_id': str(report_id),
        'template_used': template_type,
        'sections': generate_report_sections(sections, report),
        'metadata': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'template_version': '1.0',
            'word_count': count_report_words(report),
            'report_options': report_options,
        },
    }

    if format_type == 'json':
        return JsonResponse(generated_report)

    if format_type == 'pdf':
        content = generate_pdf_report(generated_report)
        response = HttpResponse(content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="comprehensive_report_{report_id}.pdf"'
        return response

    if format_type == 'word':
        content = generate_word_report(generated_report)
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        response['Content-Disposition'] = f'attachment; filename="comprehensive_report_{report_id}.docx"'
        return response

    return JsonResponse({'error': f'Unsupported format: {format_type}'}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@report_access_required
def preview_report(request, report_id):
    """Preview report before generation."""
    report = get_report(str(report_id))
    if not report:
        return JsonResponse({'error': 'Report not found'}, status=404)
    registry = get_report_prompt_registry()

    preview_data = {
        'report_id': str(report_id),
        'bank_name': report.get('bank_name'),
        'data_period': report.get('data_period'),
        'preview_sections': generate_report_sections(['executive_summary', 'statistical_highlights'], report),
        'available_templates': registry.get_templates(),
    }
    return JsonResponse(preview_data)


@require_http_methods(["POST"])
@report_access_required
@ai_rate_limited
def simple_custom_report_view(request, report_id=None):
    """AI-powered custom report view using stored report data."""
    try:
        data = json.loads(request.body) if request.body else {}
        prompt = data.get('prompt', '')
        report_options = data.get('report_options') or {
            'template': data.get('template'),
            'sections': data.get('sections') or data.get('selected_sections') or [],
            'include_sections': data.get('include_sections') or [],
            'exclude_sections': data.get('exclude_sections') or [],
            'length': data.get('length'),
            'detail_level': data.get('detail_level'),
            'output_format': data.get('format'),
        }
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)

    if not prompt:
        return JsonResponse({'error': 'Prompt is required'}, status=400)

    if not report_id:
        return JsonResponse({'error': 'Report ID is required'}, status=400)

    report = get_report(str(report_id))
    if not report:
        return JsonResponse({'error': 'Report not found'}, status=404)

    original_json = report.get('metadata', {}).get('original_json')
    if not original_json:
        return JsonResponse({'error': 'Original JSON data not found in report'}, status=400)

    sections, error_msg, ai_enhanced = generate_analysis_from_prompt(
        prompt,
        original_json,
        report.get('ai_analysis', {}),
        report_options=report_options,
    )

    if sections and ai_enhanced:
        generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        metadata = dict(report.get('metadata', {}))
        metadata.update({
            'user_prompt': prompt,
            'comprehensive_generated': True,
            'generated_at': generated_at,
        })
        update_report(str(report_id), {
            'comprehensive_analysis': sections,
            'user_prompt': prompt,
            'ai_enhanced': True,
            'status': 'completed',
            'metadata': metadata,
        }, request=request)
        return JsonResponse({
            'success': True,
            'report_id': str(report_id),
            'comprehensive_analysis': sections,
            'generated_at': generated_at,
            'prompt': prompt,
        })

    return JsonResponse({
        'success': False,
        'error': error_msg or 'AI analysis failed',
    }, status=500)

__all__ = [
    'BenchmarkComparisonView',
    'CustomReportView',
    'ExportReportView',
    'FinancialReportViewSet',
    'MetricsSummaryView',
    'ReportDetailView',
    'TrendAnalysisView',
    'generate_comprehensive_report',
    'get_insights',
    'get_report_templates',
    'get_report_prompt_config',
    'get_analysis_prompts',
    'update_analysis_prompt_view',
    'reset_analysis_prompts_view',
    'preview_report',
    'regenerate_insights',
    'update_report_prompt_config',
    'simple_custom_report_view',
    'simple_export_view',
    'simple_report_detail_view',
    'simple_reports_view',
    'trigger_data_cleanup',
    'preview_cleanup',
    'list_manageable_reports',
    'bulk_report_action',
]
