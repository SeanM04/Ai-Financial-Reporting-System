"""Prompt module CRUD and versioning APIs for Prompt Intelligence."""

from __future__ import annotations

import json

from django.http import JsonResponse
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from rest_framework.authentication import SessionAuthentication
from ..services.prompt_module_store import (
    compare_prompt_versions,
    create_prompt_module,
    duplicate_prompt_module,
    get_prompt_module,
    list_prompt_module_versions,
    list_prompt_modules,
    restore_prompt_module_version,
    serialize_prompt_module,
    serialize_prompt_module_version,
    update_prompt_module,
)


def _body(request) -> dict:
    """Return the request's JSON body as a dict.

    Uses DRF's parsed ``request.data`` when it is a dict, otherwise decodes the raw
    body. Returns an empty dict when the body is missing or is not valid JSON.
    """
    if isinstance(getattr(request, 'data', None), dict):
        return request.data
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return {}


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def list_prompt_modules_view(request):
    """List prompt modules, optionally filtered.

    GET /api/prompt-modules/ (any signed-in user)

    Query parameters:
        include_archived: "false" hides archived modules (default: included).
        category: exact category name, case-insensitive.
        q: text searched in the name, description, category and prompt text.
        favorites: "true" returns only modules marked as favorites.
        include_versions: "true" adds each module's version history.

    Returns the matching modules, their count, and every category name in use
    (from all modules, not just the filtered ones) for building filter menus.
    """
    include_archived = str(request.GET.get('include_archived', 'true')).lower() != 'false'
    category = (request.GET.get('category') or '').strip()
    search = (request.GET.get('q') or '').strip().lower()
    favorites_only = str(request.GET.get('favorites', '')).lower() in ('1', 'true', 'yes')
    include_versions = str(request.GET.get('include_versions', '')).lower() in ('1', 'true', 'yes')

    modules = list_prompt_modules(include_archived=include_archived)
    results = []
    for module in modules:
        if category and module.category.lower() != category.lower():
            continue
        if favorites_only and not module.is_favorite:
            continue
        if search:
            hay = f'{module.name} {module.description} {module.category} {module.prompt_text}'.lower()
            if search not in hay:
                continue
        results.append(serialize_prompt_module(module, include_versions=include_versions))

    return JsonResponse({
        'prompt_modules': results,
        'count': len(results),
        'categories': sorted({m.category for m in modules if m.category}),
    })


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def create_prompt_module_view(request):
    """Create a prompt module and record it as version 1.

    POST /api/prompt-modules/create/ (staff only)

    The JSON body may set name, slug, description, category, prompt_text,
    order_index, tags, related_sections, status and ai_settings. The slug defaults
    to one built from the name, and the status defaults to "draft".

    Returns 201 with the new module and its version history, or 400 when the slug
    is already taken.
    """
    try:
        module = create_prompt_module(_body(request), user=request.user)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'prompt_module': serialize_prompt_module(module, include_versions=True)}, status=201)


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def prompt_module_detail_view(request, module_id):
    """Return one prompt module with its full version history.

    GET /api/prompt-modules/<module_id>/ (any signed-in user)

    Returns 404 when no module has that id.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    return JsonResponse({'prompt_module': serialize_prompt_module(module, include_versions=True)})


@api_view(['PATCH', 'PUT', 'POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def update_prompt_module_view(request, module_id):
    """Edit a prompt module and record the result as a new version.

    PATCH, PUT or POST /api/prompt-modules/<module_id>/update/ (staff only)

    Only the fields present in the JSON body change: name, description, category,
    prompt_text, order_index, tags, related_sections, status, ai_settings and
    is_favorite. An optional change_comment (or comment) is stored on the new
    version. Every call creates a version, even when nothing changed.

    Returns the updated module with its version history, or 404 when no module
    has that id.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    body = _body(request)
    module = update_prompt_module(
        module,
        body,
        user=request.user,
        change_comment=body.get('change_comment') or body.get('comment') or '',
    )
    return JsonResponse({'prompt_module': serialize_prompt_module(module, include_versions=True)})


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def prompt_module_versions_view(request, module_id):
    """List every saved version of a prompt module.

    GET /api/prompt-modules/<module_id>/versions/ (any signed-in user)

    Returns the versions with their count, or 404 when no module has that id.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    versions = [serialize_prompt_module_version(v) for v in list_prompt_module_versions(module)]
    return JsonResponse({'versions': versions, 'count': len(versions)})


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def compare_prompt_module_versions_view(request, module_id):
    """Return two versions of a prompt module side by side.

    GET /api/prompt-modules/<module_id>/versions/compare/?from=<n>&to=<n>
    (any signed-in user)

    The response holds the module, both versions in full, and prompt_changed,
    which says whether their prompt text differs. It does not compute a line diff.

    Returns 404 when no module has that id, and 400 when from or to is missing,
    not a number, or names a version that does not exist.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    try:
        payload = compare_prompt_versions(module, int(request.GET.get('from')), int(request.GET.get('to')))
    except (TypeError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse(payload)


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def restore_prompt_module_version_view(request, module_id):
    """Bring back an earlier version of a prompt module.

    POST /api/prompt-modules/<module_id>/restore/ (staff only)
    Body: {"version_number": <n>}

    Copies that version's prompt text, AI settings, tags and status onto the
    module and saves the result as a new version ("Restored from version n").
    History is never rewound, so the restore can itself be undone.

    Returns the updated module with its version history, 404 when no module has
    that id, or 400 when version_number is missing, not a number, or not found.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    try:
        module = restore_prompt_module_version(module, int(_body(request).get('version_number')), user=request.user)
    except (TypeError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'prompt_module': serialize_prompt_module(module, include_versions=True)})


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAdminUser])
def duplicate_prompt_module_view(request, module_id):
    """Copy a prompt module into a new draft.

    POST /api/prompt-modules/<module_id>/duplicate/ (staff only)

    The copy is named "<name> (Copy)", gets the slug "<slug>-copy" (or
    "-copy-2", "-copy-3", ... if taken), starts at version 1 with status "draft",
    and sorts right after the original. Its version history starts fresh.

    Returns 201 with the new module, or 404 when no module has that id.
    """
    module = get_prompt_module(module_id)
    if not module:
        return JsonResponse({'error': 'Prompt module not found'}, status=404)
    copy = duplicate_prompt_module(module, user=request.user)
    return JsonResponse({'prompt_module': serialize_prompt_module(copy,
                                                                   include_versions=True)}, status=201)
