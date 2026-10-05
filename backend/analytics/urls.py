"""
URL configuration for analytics API endpoints
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api.auth import current_user_view, simple_login_view
from .api.reports import (
    BenchmarkComparisonView,
    CustomReportView,
    ExportReportView,
    FinancialReportViewSet,
    MetricsSummaryView,
    ReportDetailView,
    TrendAnalysisView,
    generate_comprehensive_report,
    get_insights,
    get_report_prompt_config,
    get_analysis_prompts,
    update_analysis_prompt_view,
    reset_analysis_prompts_view,
    get_user_settings,
    update_user_settings,
    get_report_templates,
    preview_report,
    regenerate_insights,
    update_report_prompt_config,
    simple_custom_report_view,
    simple_export_view,
    simple_report_detail_view,
    simple_reports_view,
    trigger_data_cleanup,
    preview_cleanup,
    list_manageable_reports,
    bulk_report_action,
    report_section_mappings_view,
    regenerate_report_section_view,
    report_section_history_view,
    save_master_prompt_to_dataset_prompt,
)
from .api.prompt_modules import (
    list_prompt_modules_view,
    create_prompt_module_view,
    prompt_module_detail_view,
    update_prompt_module_view,
    prompt_module_versions_view,
    compare_prompt_module_versions_view,
    restore_prompt_module_version_view,
    duplicate_prompt_module_view,
)
from .api.system import (
    SystemHealthView,
    UserUploadsView,
    ai_reset_quota_view,
    ai_status_view,
    ai_test_connection_view,
    ai_usage_stats_view,
    test_endpoint,
    test_openai_view,
)
from .api.uploads import (
    FinancialDataUploadView,
    analyze_direct_data,
    simple_custom_prompt_view,
    simple_task_status_view,
    simple_upload_view,
)

# Create router for viewsets
router = DefaultRouter()
router.register(r'reports', FinancialReportViewSet, basename='financialreport')

app_name = 'analytics'

urlpatterns = [
    # Testing
    path('test/', test_endpoint, name='test'),
    path('test-openai/', test_openai_view, name='test_openai'),
    path('simple-login/', simple_login_view, name='simple_login'),
    path('auth/me/', current_user_view, name='current_user'),
    
    # Authentication (same throttled view as simple-login/)
    path('auth/login/', simple_login_view, name='login'),
    
    # File upload and analysis
    path('simple-upload/', simple_upload_view, name='simple_upload'),
    path('simple-custom-prompt/', simple_custom_prompt_view, name='simple_custom_prompt'),
    path('upload/', FinancialDataUploadView.as_view(), name='upload'),
    
    # Simple reports endpoints
    path('simple-reports/', simple_reports_view, name='simple_reports'),
    path('simple-reports/<uuid:report_id>/', simple_report_detail_view, name='simple_report_detail'),
    path('media/<uuid:report_id>/<str:file_type>/', simple_export_view, name='media_retrieve'),
    path('files/<uuid:report_id>/', simple_export_view, name='file_download'),
    path('simple-reports/<uuid:report_id>/download/', simple_export_view, name='simple_download'),
    path('simple-reports/<uuid:report_id>/export/', simple_export_view, name='simple_export'),
    path('simple-reports/<uuid:report_id>/simple-custom-report/', simple_custom_report_view),
    # Generate comprehensive report
    path('simple-reports/<uuid:report_id>/generate/', generate_comprehensive_report),
    # Get report templates
    path('simple-reports/templates/', get_report_templates),
    path('simple-reports/prompt-config/', get_report_prompt_config),
    path('simple-reports/prompt-config/update/', update_report_prompt_config),
    path('analysis-prompts/', get_analysis_prompts),
    path('analysis-prompts/update/', update_analysis_prompt_view),
    path('analysis-prompts/reset/', reset_analysis_prompts_view),
    # Prompt Intelligence — Overleaf-style source/output pairing
    path('prompt-modules/', list_prompt_modules_view),
    path('prompt-modules/create/', create_prompt_module_view),
    path('prompt-modules/<int:module_id>/', prompt_module_detail_view),
    path('prompt-modules/<int:module_id>/update/', update_prompt_module_view),
    path('prompt-modules/<int:module_id>/versions/', prompt_module_versions_view),
    path('prompt-modules/<int:module_id>/versions/compare/', compare_prompt_module_versions_view),
    path('prompt-modules/<int:module_id>/restore/', restore_prompt_module_version_view),
    path('prompt-modules/<int:module_id>/duplicate/', duplicate_prompt_module_view),
    path('simple-reports/<uuid:report_id>/section-mappings/', report_section_mappings_view),
    path(
        'simple-reports/<uuid:report_id>/sections/<str:section_key>/regenerate/',
        regenerate_report_section_view,
    ),
    path(
        'simple-reports/<uuid:report_id>/sections/<str:section_key>/history/',
        report_section_history_view,
    ),
    path(
        'simple-reports/<uuid:report_id>/save-master-prompt/',
        save_master_prompt_to_dataset_prompt,
    ),
    # User settings (per-user persisted preferences)
    path('user-settings/', get_user_settings),
    path('user-settings/update/', update_user_settings),
    # Preview report
    path('simple-reports/<uuid:report_id>/preview/', preview_report),
    path('analyze/direct/', analyze_direct_data, name='analyze_direct'),
    
    # Task status and progress
    path('tasks/<str:task_id>/', simple_task_status_view, name='task_status'),
    
    # Report endpoints
    path('reports/', include(router.urls)),
    path('reports/<uuid:report_id>/', ReportDetailView.as_view(), name='report_detail'),
    path('reports/<uuid:report_id>/metrics/', MetricsSummaryView.as_view(), name='report_metrics'),
    path('reports/<uuid:report_id>/trends/', TrendAnalysisView.as_view(), name='report_trends'),
    path('reports/<uuid:report_id>/benchmark/', BenchmarkComparisonView.as_view(), name='report_benchmark'),
    path('reports/<uuid:report_id>/export/', ExportReportView.as_view(), name='report_export'),
    
    # Insights endpoints
    path('reports/<uuid:report_id>/insights/', get_insights, name='report_insights'),
    path('reports/<uuid:report_id>/insights/regenerate/', regenerate_insights, name='regenerate_insights'),
    
    # Custom report endpoints
    path('reports/<uuid:report_id>/custom-report/', CustomReportView.as_view(), name='custom_report'),
    path('reports/<uuid:report_id>/simple-custom-report/', simple_custom_report_view, name='simple_custom_report'),
    
    # User endpoints
    path('uploads/', UserUploadsView.as_view(), name='user_uploads'),
    
    # Data cleanup endpoints
    path('cleanup/', trigger_data_cleanup, name='trigger_cleanup'),
    path('cleanup/preview/', preview_cleanup, name='preview_cleanup'),
    path('reports/manage/', list_manageable_reports, name='reports_manage'),
    path('reports/manage/bulk-action/', bulk_report_action, name='reports_manage_bulk_action'),
    
    # System endpoints
    path('health/', SystemHealthView.as_view(), name='system_health'),
    
    # AI Status and Management endpoints
    path('ai/status/', ai_status_view, name='ai_status'),
    path('ai/usage/', ai_usage_stats_view, name='ai_usage_stats'),
    path('ai/reset-quota/', ai_reset_quota_view, name='ai_reset_quota'),
    path('ai/test/', ai_test_connection_view, name='ai_test'),
]
