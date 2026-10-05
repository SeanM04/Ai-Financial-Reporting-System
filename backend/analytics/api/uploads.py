"""Upload and analysis endpoints."""

import json
import uuid
import logging
from datetime import datetime
from typing import Any

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .access import ai_rate_limited, api_login_required, report_access_required
from ..services.report_store import get_report, save_report, update_report, user_can_access_report
from django.conf import settings
from ..services.prompt_module_store import apply_section_traceability
from ..services.analysis_prompt_defaults import (
    FINANCIAL_INSTRUMENTS_ANALYSIS_ID,
    MONEY_MARKET_ANALYSIS_ID,
    WACC_ANALYSIS_ID,
)
from ..services.prompt_settings_store import get_analysis_prompt
from ..views import (
    AnalysisStatusView,
    FinancialDataUploadView,
    analyze_direct_data,
    build_data_summary,
    extract_entity_metadata,
    generate_analysis_from_prompt,
    generate_comprehensive_ai_analysis,
    normalize_json_for_analysis,
    perform_initial_ai_analysis,
)


logger = logging.getLogger(__name__)


DATASET_TYPE_RULES: dict[str, dict[str, Any]] = {
    'wacc': {
        'label': 'WACC',
        'prompt_id': WACC_ANALYSIS_ID,
        'template': 'wacc_report',
        'signals': [
            ['wacc', 'weighted average cost of capital'],
            ['cost of equity', 'cost_equity', 'equity cost'],
            ['cost of debt', 'cost_debt', 'debt cost'],
            ['tax rate', 'tax_rate', 'effective tax rate'],
            ['capital structure', 'capital_structure', 'equity ratio', 'debt ratio'],
            ['beta', 'risk free rate', 'risk-free rate', 'market risk premium'],
        ],
        'missing_indicators': ['cost of equity', 'cost of debt',
                                'tax rate', 'capital structure', 'WACC'],
    },
    'money_market': {
        'label': 'Money Market',
        'prompt_id': MONEY_MARKET_ANALYSIS_ID,
        'template': 'money_market_report',
        'signals': [
            ['treasury bill', 'treasury bills', 'tbill', 't-bill'],
            ['commercial paper'],
            ['certificate of deposit', 'certificates of deposit', 'cd rate', 'cds'],
            ['interbank rate', 'interbank rates'],
            ['repo rate', 'repo rates', 'repurchase agreement'],
            ['liquidity', 'short-term', 'short term'],
        ],
        'missing_indicators': ['treasury bills', 'commercial paper',
                                'certificates of deposit', 'interbank rates', 'repo rates'],
    },
    'financial_instruments': {
        'label': 'Financial Instruments',
        'prompt_id': FINANCIAL_INSTRUMENTS_ANALYSIS_ID,
        'template': 'financial_instruments_report',
        'signals': [
            ['bond', 'bonds', 'treasury security', 'treasury securities'],
            ['equity', 'equities', 'stock', 'shares'],
            ['derivative', 'derivatives', 'option', 'swap', 'future', 'futures'],
            ['mutual fund', 'mutual funds'],
            ['etf', 'exchange-traded fund', 'exchange traded fund'],
            ['commodity', 'commodities', 'foreign exchange', 'fx'],
        ],
        'missing_indicators': ['bonds', 'equities', 'derivatives', 'mutual funds', 'ETFs'],
    },
}


def _collect_text_tokens(value: Any, parts: list[str], depth: int = 0) -> None:
    """Append every key, string and number found in a JSON value to ``parts``.

    Used to build the text that dataset-type detection searches. Walks nested
    objects and arrays up to 8 levels deep and reads only the first 100 items of
    each array, so very large files stay fast. Booleans and nulls are skipped.
    """
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            parts.append(str(key))
            _collect_text_tokens(item, parts, depth + 1)
    elif isinstance(value, list):
        for item in value[:100]:
            _collect_text_tokens(item, parts, depth + 1)
    elif isinstance(value, str):
        parts.append(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parts.append(str(value))


def _infer_dataset_type(json_data: Any) -> str | None:
    """Guess which dataset type a JSON file contains from its keys and values.

    Each type in DATASET_TYPE_RULES has six keyword groups. A type scores one
    point for every group with at least one keyword in the file (case-insensitive
    substring match). The highest score wins, with ties going to the type listed
    first in DATASET_TYPE_RULES.

    Returns the winning type key, or None when no type matches at least two groups.
    """
    parts: list[str] = []
    _collect_text_tokens(json_data, parts)
    text = ' '.join(parts).lower()

    scores: dict[str, int] = {}
    for dataset_type, rule in DATASET_TYPE_RULES.items():
        score = 0
        for signal_group in rule['signals']:
            if any(signal in text for signal in signal_group):
                score += 1
        scores[dataset_type] = score

    if not scores:
        return None

    best_type = max(scores, key=scores.get)
    if scores[best_type] < 2:
        return None
    return best_type


def _validate_dataset_type(json_data: Any, selected_type: str) -> tuple[bool, str | None, str | None]:
    """Check that an uploaded file matches the dataset type the user selected.

    Returns ``(is_valid, detected_type, error_message)``:

    - ``(True, type, None)`` when detection agrees with the selection.
    - ``(False, other_type, message)`` when the file looks like a different type;
      the message names both types.
    - ``(False, None, message)`` when the selection is not a known type, or when no
      type could be detected; the message lists indicators the file should contain.
    """
    selected = (selected_type or '').strip().lower()
    if selected not in DATASET_TYPE_RULES:
        return False, None, 'Please select one dataset type before uploading: WACC, Money Market, or Financial Instruments.'

    detected = _infer_dataset_type(json_data)
    if detected == selected:
        return True, detected, None

    selected_label = DATASET_TYPE_RULES[selected]['label']
    if detected:
        detected_label = DATASET_TYPE_RULES[detected]['label']
        return (
            False,
            detected,
            f'The uploaded data appears to contain {detected_label} information, but you selected {selected_label}. Please either upload the correct dataset or change the dataset type.',
        )

    missing = ', '.join(DATASET_TYPE_RULES[selected]['missing_indicators'])
    return (
        False,
        None,
        f'Unable to verify the selected {selected_label} dataset. Required indicators such as {missing} were not found in the uploaded file.',
    )


@require_http_methods(["POST"])
@api_login_required
@ai_rate_limited
def simple_custom_prompt_view(request):
    """Answer a custom analysis prompt about an existing report's data.

    POST /api/simple-custom-prompt/ (signed-in owner of the report, or staff;
    counts towards the hourly AI limit)
    Body: {"report_id": "...", "prompt": "...", "report_options": {...}}

    Sends the report's original uploaded JSON and the prompt to OpenAI and returns
    the generated sections in ``analysis``. Nothing is saved to the report.
    Only real AI output is returned: rule-based fallback sections would ignore the
    prompt, so when AI is unavailable the response is 503 with the reason.

    Returns 400 for a missing report_id or prompt, invalid JSON, or a report with
    no stored original data, and 404 when the report doesn't exist or belongs to
    someone else.
    """
    try:
        body = json.loads(request.body)
        report_id = body.get('report_id')
        custom_prompt = body.get('prompt')
        report_options = body.get('report_options') or {}

        if not report_id or not custom_prompt:
            return JsonResponse({'error': 'report_id and prompt are required'}, status=400)

        report = get_report(str(report_id)) if user_can_access_report(request.user, str(report_id)) else None
        if not report:
            return JsonResponse({'error': 'Report not found'}, status=404)

        original_json = report.get('metadata', {}).get('original_json')
        if not original_json:
            return JsonResponse({'error': 'Original JSON data not found in report'}, status=400)

        analysis_result = generate_comprehensive_ai_analysis({
            'bank_name': report.get('bank_name', 'Unknown Bank'),
            'data_period': report.get('data_period', 'Unknown Period'),
            'financial_data': original_json,
            'existing_analysis': report.get('ai_analysis', {}),
            'data_summary': {
                'keys_found': list(original_json.keys()) if isinstance(original_json, dict) else 'Non-dict JSON',
                'data_size': len(str(original_json)),
                'sample_data': original_json if isinstance(original_json, dict) else {'data': original_json},
            },
            'user_prompt': custom_prompt,
            'report_options': report_options,
        })

        # Rule-based fallback sections ignore the custom prompt, so only AI output answers it.
        if analysis_result and analysis_result.get('success') and analysis_result.get('ai_enhanced'):
            return JsonResponse({
                'success': True,
                'analysis': analysis_result.get('sections', []),
                'report_id': report_id,
            })

        error_msg = analysis_result.get('error', 'Unknown error') if analysis_result else 'Unknown error'
        return JsonResponse({'success': False, 'error': error_msg}, status=503)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
    except Exception:
        logger.exception('Custom prompt analysis failed')
        return JsonResponse({'error': 'Custom analysis failed.'}, status=500)


@require_http_methods(["POST"])
@api_login_required
@ai_rate_limited
def simple_upload_view(request):
    """Upload a JSON dataset and generate its report in the same request.

    POST /api/simple-upload/ (signed in; counts towards the hourly AI limit)

    Multipart form fields:
        file: the dataset, a UTF-8 ``.json`` file.
        dataset_type: "wacc", "money_market" or "financial_instruments".
        description: optional free text stored with the report.
        report_options: optional JSON string with template, sections,
            include_sections, exclude_sections, length, detail_level and
            output_format. length, detail_level and output_format may also be
            sent as separate fields. A JSON request body is accepted too.

    Steps:
        1. Check the file and confirm its contents match the dataset type.
        2. Load that type's analysis prompt and split it into one prompt per
           report section, filling gaps from the section's prompt module.
        3. Save a "processing" report owned by the uploader, and save each
           section prompt onto its prompt module as a new version.
        4. Ask OpenAI for every section. If AI is unavailable, rule-based
           sections are used and the reason is returned as a warning.
        5. Save the completed report and record which prompt version produced
           each section.

    Returns the complete report plus ``message``, and ``warning`` /
    ``warning_code`` when AI was unavailable. Returns 400 when the file is
    missing, not ``.json``, not UTF-8, invalid or empty JSON, when no dataset type
    was chosen, or when the contents don't match it. Because generation happens
    inside this request, a response can take a few minutes.
    """
    try:
        body = {}
        # Only JSON requests carry options in the body. For multipart uploads the CSRF
        # check has already read the form, and touching request.body would raise.
        if request.content_type == 'application/json' and request.body:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError:
                body = {}

        report_options = {}
        raw_report_options = request.POST.get('report_options') or body.get('report_options')
        if raw_report_options:
            try:
                report_options = json.loads(raw_report_options) if isinstance(raw_report_options, str) else raw_report_options
            except json.JSONDecodeError:
                report_options = {}

        selected_dataset_type = (
            request.POST.get('dataset_type')
            or body.get('dataset_type')
            or report_options.get('dataset_type')
            or ''
        ).strip().lower()

        if 'file' not in request.FILES:
            return JsonResponse({'error': 'No file provided'}, status=400)

        uploaded_file = request.FILES['file']
        description = request.POST.get('description', '')

        if not selected_dataset_type:
            return JsonResponse({
                'error': 'Please select one dataset type before uploading: WACC, Money Market, or Financial Instruments.',
            }, status=400)

        if not uploaded_file.name.endswith('.json'):
            return JsonResponse({'error': 'Only JSON files are supported'}, status=400)

        file_content = uploaded_file.read().decode('utf-8')
        json_data = json.loads(file_content)

        if json_data is None:
            return JsonResponse({'error': 'JSON file is empty'}, status=400)

        is_dataset_validated, detected_dataset_type, validation_error = _validate_dataset_type(
            json_data,
            selected_dataset_type,
        )
        if not is_dataset_validated:
            response = {
                'error': validation_error or 'Uploaded dataset does not match the selected type.',
                'selected_dataset_type': selected_dataset_type,
            }
            if detected_dataset_type:
                response['detected_dataset_type'] = detected_dataset_type
            return JsonResponse(response, status=400)

        normalized_json, original_json = normalize_json_for_analysis(json_data)
        bank_name, data_period = extract_entity_metadata(json_data)

        dataset_rule = DATASET_TYPE_RULES[selected_dataset_type]
        dataset_prompt = get_analysis_prompt(dataset_rule['prompt_id'])
        analysis_request_prompt = (dataset_prompt.content if dataset_prompt else '').strip()
        if not analysis_request_prompt:
            return JsonResponse({'error': 'Selected dataset analysis prompt is unavailable.'}, status=500)

        # Prefer sections from the upload UI; otherwise use the dataset template sections.
        client_sections = report_options.get('sections') or report_options.get('include_sections') or []
        if isinstance(client_sections, str):
            client_sections = [client_sections]
        client_sections = [str(s).strip() for s in client_sections if str(s).strip()]

        resolved_report_options = {
            'template': report_options.get('template') or dataset_rule['template'],
            'sections': client_sections,
            'include_sections': report_options.get('include_sections') or client_sections,
            'exclude_sections': report_options.get('exclude_sections') or [],
            'length': (
                request.POST.get('length')
                or body.get('length')
                or report_options.get('length')
                or 'standard'
            ),
            'detail_level': (
                request.POST.get('detail_level')
                or body.get('detail_level')
                or report_options.get('detail_level')
                or 'balanced'
            ),
            'output_format': (
                request.POST.get('output_format')
                or body.get('output_format')
                or report_options.get('output_format')
                or 'json'
            ),
            'dataset_type': selected_dataset_type,
        }

        report_id = str(uuid.uuid4())
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Resolve the final section list exactly the same way the AI does.
        from ..services.report_prompt_registry import get_report_prompt_registry
        resolved_report_options = get_report_prompt_registry().build_report_options(resolved_report_options)
        section_keys = resolved_report_options.get('sections') or []

        # Decompose the dataset prompt the user selected into per-section instruction blocks.
        # The UI's left editors and regen behavior will be based on these exact blocks.
        from ..services.prompt_module_store import decompose_master_prompt_to_section_prompts

        section_prompt_overrides = decompose_master_prompt_to_section_prompts(
            analysis_request_prompt,
            section_keys,
        )

        # Safety fallback: if decomposition didn't find a block for a section,
        # use the existing active prompt-module text / registry prompt so editors are never empty.
        try:
            from ..services.prompt_module_store import get_prompt_module_for_section, get_section_prompt_text

            for section_key in section_keys or []:
                section_key = str(section_key).strip()
                if not section_key:
                    continue
                if (section_prompt_overrides.get(section_key) or '').strip():
                    continue
                module = get_prompt_module_for_section(section_key)
                fallback_text = None
                if module and (module.prompt_text or '').strip():
                    fallback_text = (module.prompt_text or '').strip()
                    fallback_text = (
                        fallback_text.replace('{bank_name}', str(bank_name))
                        .replace('{data_period}', str(data_period))
                    )
                if not fallback_text:
                    fallback_text = get_section_prompt_text(
                        section_key,
                        {'bank_name': bank_name, 'data_period': data_period},
                    )
                if fallback_text:
                    section_prompt_overrides[section_key] = fallback_text
        except Exception:
            pass

        def _compose_master_prompt_from_section_prompts(keys: list[str], prompts: dict[str, str]) -> str:
            """Join per-section prompts into one master prompt with section markers.

            Each section becomes a ``[SECTION:key] ... [/SECTION]`` block, in the
            order of ``keys``, separated by blank lines. Blank keys are skipped.
            """
            parts: list[str] = []
            for k in keys or []:
                section_key = str(k).strip()
                if not section_key:
                    continue
                parts.append(f"[SECTION:{section_key}]\n{(prompts.get(section_key) or '').strip()}\n[/SECTION]")
            return "\n\n".join(parts)

        # Prefer the exact Dataset Analysis Prompt the user selected when it already
        # contains section markers. Otherwise compose a marker document from extracts.
        has_markers = bool(section_prompt_overrides) and any(
            f'[SECTION:{k}]' in analysis_request_prompt for k in section_keys
        )
        if has_markers:
            master_prompt = analysis_request_prompt
        else:
            composed = _compose_master_prompt_from_section_prompts(section_keys, section_prompt_overrides)
            master_prompt = composed if composed.strip() else analysis_request_prompt

        processing_report_data = {
            'id': report_id,
            'filename': uploaded_file.name,
            'size': uploaded_file.size,
            'description': description,
            'user_prompt': master_prompt,
            'dataset_type': selected_dataset_type,
            'detected_dataset_type': detected_dataset_type or selected_dataset_type,
            'task_id': report_id,
            'uploaded_at': current_time,
            'status': 'processing',
            'progress': 35,
            'bank_name': bank_name,
            'data_period': data_period,
            'report_options': resolved_report_options,
            'metadata': {
                'title': dataset_rule['label'],
                'report_date': current_time,
                'period': data_period,
                'generated_at': current_time,
                'ai_processed': False,
                'comprehensive_generated': False,
                'ai_enhanced': False,
                'user_prompt': master_prompt,
                'dataset_type': selected_dataset_type,
                'detected_dataset_type': detected_dataset_type or selected_dataset_type,
                'original_json': original_json,
                'normalized_json': normalized_json,
                'report_options': resolved_report_options,
            },
        }
        save_report(report_id, processing_report_data, request=request)

        # Best-effort: seed prompt modules so traceability aligns with the upload-selected prompt.
        # This creates prompt module versions to keep history.
        try:
            from ..services.prompt_module_store import get_prompt_module_for_section, update_prompt_module

            for section_key in section_keys or []:
                prompt_text = (section_prompt_overrides.get(section_key) or '').strip()
                if not prompt_text:
                    continue
                module = get_prompt_module_for_section(section_key)
                if module and (module.prompt_text or '').strip() != prompt_text:
                    payload: dict[str, Any] = {'prompt_text': prompt_text}
                    if getattr(module, 'status', None) != 'active':
                        payload['status'] = 'active'
                    update_prompt_module(
                        module,
                        payload,
                        user=request.user,
                        change_comment=f'Seeded from dataset analysis prompt for {selected_dataset_type}',
                    )
        except Exception:
            # Never fail report generation if prompt-module seeding fails.
            pass

        ai_analysis = perform_initial_ai_analysis(json_data)

        full_ai_analysis, ai_error_msg, ai_enhanced = generate_analysis_from_prompt(
            analysis_request_prompt,
            original_json,
            ai_analysis,
            report_options=resolved_report_options,
            section_prompt_overrides=section_prompt_overrides,
        )
        comprehensive_generated = len(full_ai_analysis) > 0 and ai_enhanced

        prompt_title = dataset_rule['label']

        report_data = {
            'id': report_id,
            'filename': uploaded_file.name,
            'size': uploaded_file.size,
            'description': description,
            'user_prompt': master_prompt,
            'dataset_type': selected_dataset_type,
            'detected_dataset_type': detected_dataset_type or selected_dataset_type,
            'task_id': report_id,
            'uploaded_at': current_time,
            'status': 'completed',
            'data_summary': {
                **build_data_summary(original_json),
                'ai_insights': ai_analysis,
            },
            'bank_name': bank_name,
            'data_period': data_period,
            'ai_analysis': ai_analysis,
            'comprehensive_analysis': full_ai_analysis,
            'ai_enhanced': ai_enhanced,
            'report_options': resolved_report_options,
            'metadata': {
                'title': prompt_title,
                'report_date': current_time,
                'period': data_period,
                'generated_at': current_time,
                'ai_processed': comprehensive_generated,
                'comprehensive_generated': comprehensive_generated,
                'ai_enhanced': ai_enhanced,
                'user_prompt': master_prompt,
                'dataset_type': selected_dataset_type,
                'detected_dataset_type': detected_dataset_type or selected_dataset_type,
                'original_json': original_json,
                'normalized_json': normalized_json,
                'report_options': resolved_report_options,
            },
        }

        if ai_error_msg:
            report_data['ai_error'] = ai_error_msg

        save_report(report_id, report_data, request=request)

        if full_ai_analysis:
            try:
                annotated = apply_section_traceability(
                    report_id,
                    full_ai_analysis,
                    report_options=resolved_report_options,
                    ai_model=getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini') if ai_enhanced else 'fallback',
                    confidence_score=0.85 if ai_enhanced else 0.5,
                    regeneration_reason='initial generation',
                )
                report_data['comprehensive_analysis'] = annotated
                update_report(report_id, {'comprehensive_analysis': annotated}, request=request)
            except Exception as exc:
                logger.debug('Section traceability skipped for %s: %s', report_id, exc)

        response_data = {
            'success': True,
            'message': 'Report generated from your analysis prompt'
            if comprehensive_generated
            else 'Report uploaded successfully, but AI-enhanced analysis is currently unavailable.',
            'task_id': report_id,
            'id': report_id,
            'report_id': report_id,
        }
        if ai_error_msg:
            response_data['warning'] = ai_error_msg
            response_data['warning_code'] = (
                'openai_quota_exceeded' if 'quota' in ai_error_msg.lower() else 'ai_unavailable'
            )

        response_data['dataset_type'] = selected_dataset_type
        response_data['detected_dataset_type'] = detected_dataset_type or selected_dataset_type

        for key, value in report_data.items():
            response_data[key] = value

        return JsonResponse(response_data)
    except UnicodeDecodeError:
        return JsonResponse({'error': 'Uploaded file must be valid UTF-8 JSON'}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON file'}, status=400)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


@require_http_methods(["GET"])
@report_access_required
def simple_task_status_view(request, task_id):
    """Report the processing status of an upload.

    GET /api/tasks/<task_id>/ (signed-in owner of the report, or staff)

    The task id is the report id returned by the upload. The response holds
    ``status`` ("processing", "completed" or "failed"), ``progress`` (35 while
    processing, otherwise 100 unless the report stored its own value) and a short
    message. Uploads finish within their own request, so by the time a client can
    ask, the status is normally "completed".

    Returns 404 when the report doesn't exist or belongs to someone else.
    """
    report_id = str(task_id)
    report = get_report(report_id)
    if not report:
        return JsonResponse({
            'id': task_id,
            'status': 'failed',
            'progress': 0,
            'message': 'Task not found',
            'error_message': 'No matching report exists for this task id.',
        }, status=404)

    status_value = str(report.get('status') or 'completed')
    if status_value == 'processing':
        progress = int(report.get('progress') or 35)
    elif status_value == 'failed':
        progress = int(report.get('progress') or 100)
    else:
        progress = int(report.get('progress') or 100)

    return JsonResponse({
        'id': task_id,
        'status': status_value,
        'progress': progress,
        'message': (
            'Analysis completed successfully'
            if status_value == 'completed'
            else 'Analysis is still processing'
            if status_value == 'processing'
            else 'Analysis failed'
        ),
        'result_data': {
            'report_id': report_id,
            'report_status': status_value,
        },
    })

__all__ = [
    'AnalysisStatusView',
    'FinancialDataUploadView',
    'analyze_direct_data',
    'simple_custom_prompt_view',
    'simple_task_status_view',
    'simple_upload_view',
]
