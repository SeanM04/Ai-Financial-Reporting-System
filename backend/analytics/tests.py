import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from financial_analytics.middleware import HideServerErrorDetailsMiddleware

from .models import PersistedReport, ReportSectionMapping
from .services.report_store import get_report, save_report
from .views import generate_analysis_from_prompt, generate_comprehensive_ai_analysis


WACC_DATA = {
    'company_name': 'Test Holdings',
    'period': '2026-06-30',
    'wacc': 0.112,
    'cost_equity': 0.145,
    'cost_debt': 0.08,
    'tax_rate': 0.25,
    'capital_structure': {'equity_share': 0.6, 'debt_share': 0.4},
    'beta': 1.1,
}

WACC_SECTIONS = [
    'executive_summary',
    'wacc_analysis',
    'financial_ratios',
    'risk_assessment',
    'benchmark_comparison',
    'recommendations',
]


def _context():
    return {
        'bank_name': 'Test Holdings',
        'data_period': '2026-06-30',
        'financial_data': WACC_DATA,
        'raw_financial_data': WACC_DATA,
        'data_summary': {},
        'user_prompt': 'Analyse the WACC data',
        'report_options': {'template': 'wacc_report'},
    }


def _fake_openai_client(content):
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        model='gpt-4o-mini',
    )
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


def _save_report(owner=None, **fields):
    """Save a minimal report; owner=None leaves it unowned."""
    report_id = str(uuid.uuid4())
    data = {
        'status': 'completed',
        'report_options': {'sections': ['executive_summary']},
        'comprehensive_analysis': [
            {'section_key': 'executive_summary', 'title': 'Executive Summary', 'content': {'content': 'Original text'}},
        ],
        'metadata': {'original_json': WACC_DATA},
        **fields,
    }
    save_report(report_id, data, request=SimpleNamespace(user=owner) if owner else None)
    return report_id


def _upload_file():
    return SimpleUploadedFile('wacc.json', json.dumps(WACC_DATA).encode(), content_type='application/json')


def _ai_reply():
    return json.dumps({
        'sections': [
            {'section_key': key, 'title': key.replace('_', ' ').title(), 'content': {'content': f'AI text for {key}'}}
            for key in WACC_SECTIONS
        ]
    })


class SimpleLoginTests(TestCase):
    def setUp(self):
        User.objects.create_user('analyst', email='analyst@example.com', password='Correct-horse-1')

    def _login(self, username, password):
        return self.client.post(
            '/api/simple-login/',
            data=json.dumps({'username': username, 'password': password}),
            content_type='application/json',
        )

    def test_login_with_username(self):
        response = self._login('analyst', 'Correct-horse-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['username'], 'analyst')

    def test_login_with_email(self):
        response = self._login('analyst@example.com', 'Correct-horse-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['username'], 'analyst')

    def test_login_with_email_ignores_case_and_spaces(self):
        response = self._login('  Analyst@Example.COM ', 'Correct-horse-1')
        self.assertEqual(response.status_code, 200)

    def test_shared_email_logs_in_account_whose_password_matches(self):
        User.objects.create_user('analyst2', email='analyst@example.com', password='Other-pass-2')
        response = self._login('analyst@example.com', 'Other-pass-2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['username'], 'analyst2')

    def test_wrong_password_is_rejected(self):
        self.assertEqual(self._login('analyst@example.com', 'wrong').status_code, 401)
        self.assertEqual(self._login('analyst', 'wrong').status_code, 401)

    def test_unknown_email_is_rejected(self):
        self.assertEqual(self._login('nobody@example.com', 'Correct-horse-1').status_code, 401)


class AIFallbackFlagTests(TestCase):
    @override_settings(OPENAI_API_KEY='')
    @patch.dict(os.environ, {'OPENAI_API_KEY': ''})
    def test_missing_key_returns_rule_based_sections_flagged_not_ai(self):
        result = generate_comprehensive_ai_analysis(_context())
        self.assertTrue(result['success'])
        self.assertFalse(result['ai_enhanced'])
        self.assertTrue(result['sections'])
        self.assertIn('API key not configured', result['error'])

    @override_settings(OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI', side_effect=RuntimeError('boom'))
    def test_api_error_returns_rule_based_sections_flagged_not_ai(self, _openai):
        result = generate_comprehensive_ai_analysis(_context())
        self.assertTrue(result['success'])
        self.assertFalse(result['ai_enhanced'])
        self.assertTrue(result['sections'])
        self.assertIn('boom', result['error'])

    @override_settings(OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI', side_effect=RuntimeError('boom'))
    def test_wrapper_passes_fallback_flag_and_reason_through(self, _openai):
        sections, error_msg, ai_enhanced = generate_analysis_from_prompt('Analyse', WACC_DATA, {})
        self.assertTrue(sections)
        self.assertFalse(ai_enhanced)
        self.assertIn('boom', error_msg)

    @override_settings(OPENAI_API_KEY='sk-test')
    def test_wrapper_reports_real_ai_output_as_ai(self):
        with patch('openai.OpenAI', return_value=_fake_openai_client(_ai_reply())):
            sections, error_msg, ai_enhanced = generate_analysis_from_prompt(
                'Analyse', WACC_DATA, {}, report_options={'template': 'wacc_report'},
            )
        self.assertTrue(ai_enhanced)
        self.assertEqual(error_msg, '')
        self.assertEqual(sections[0]['content']['content'], 'AI text for executive_summary')


class UploadAIFlagTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('uploader', password='Uploader-pass-1'))

    def _upload(self):
        return self.client.post('/api/simple-upload/', {'file': _upload_file(), 'dataset_type': 'wacc'})

    @override_settings(OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI', side_effect=RuntimeError('boom'))
    def test_fallback_upload_is_labelled_and_warns(self, _openai):
        response = self._upload()
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertFalse(data['ai_enhanced'])
        self.assertIn('boom', data['warning'])
        self.assertEqual(data['warning_code'], 'ai_unavailable')
        self.assertIn('boom', data['ai_error'])
        self.assertFalse(data['metadata']['comprehensive_generated'])
        self.assertTrue(data['comprehensive_analysis'])
        for section in data['comprehensive_analysis']:
            self.assertEqual(section['trace']['ai_model'], 'fallback')
            self.assertEqual(section['trace']['confidence_score'], 0.5)

        mappings = ReportSectionMapping.objects.filter(report_id=data['id'])
        self.assertTrue(mappings.exists())
        self.assertEqual(set(mappings.values_list('ai_model', flat=True)), {'fallback'})

    @override_settings(OPENAI_API_KEY='sk-test', OPENAI_MODEL='gpt-4o-mini')
    def test_ai_upload_is_labelled_as_ai(self):
        with patch('openai.OpenAI', return_value=_fake_openai_client(_ai_reply())):
            response = self._upload()
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data['ai_enhanced'])
        self.assertNotIn('warning', data)
        self.assertTrue(data['metadata']['comprehensive_generated'])
        for section in data['comprehensive_analysis']:
            self.assertEqual(section['trace']['ai_model'], 'gpt-4o-mini')


class SectionRegenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('editor', password='Editor-pass-1')
        self.client.force_login(self.user)
        self.report_id = _save_report(owner=self.user)

    def test_fallback_result_does_not_replace_the_section(self):
        fallback = {
            'success': True,
            'ai_enhanced': False,
            'error': 'OpenAI API error: boom',
            'sections': [
                {'section_key': 'executive_summary', 'title': 'Executive Summary', 'content': {'content': 'Rule text'}},
            ],
        }
        with patch('analytics.views.generate_comprehensive_ai_analysis', return_value=fallback):
            response = self.client.post(
                f'/api/simple-reports/{self.report_id}/sections/executive_summary/regenerate/',
                data=json.dumps({'prompt': 'Rewrite the summary', 'reason': 'test'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'OpenAI API error: boom')
        section = get_report(self.report_id)['comprehensive_analysis'][0]
        self.assertEqual(section['content']['content'], 'Original text')


class ReportAccessTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user('alice', password='Alice-pass-1')
        self.bob = User.objects.create_user('bob', password='Bob-pass-1')
        self.staff = User.objects.create_user('auditor', password='Staff-pass-1', is_staff=True)
        self.alice_report = _save_report(owner=self.alice)
        self.unowned_report = _save_report(owner=None)

    def test_anonymous_requests_are_rejected(self):
        for url in (
            '/api/simple-reports/',
            f'/api/simple-reports/{self.alice_report}/',
            f'/api/simple-reports/{self.alice_report}/export/?format=json',
            f'/api/tasks/{self.alice_report}/',
        ):
            self.assertEqual(self.client.get(url).status_code, 401, url)
        upload = self.client.post('/api/simple-upload/', {'file': _upload_file(), 'dataset_type': 'wacc'})
        self.assertEqual(upload.status_code, 401)
        # DRF endpoints now require login by default.
        self.assertIn(self.client.get('/api/simple-reports/templates/').status_code, (401, 403))

    def test_users_list_only_their_own_reports(self):
        self.client.force_login(self.alice)
        data = self.client.get('/api/simple-reports/').json()
        self.assertEqual([r['id'] for r in data['results']], [self.alice_report])
        self.assertNotIn('debug', data)

        self.client.force_login(self.bob)
        self.assertEqual(self.client.get('/api/simple-reports/').json()['count'], 0)

    def test_other_users_report_returns_404(self):
        self.client.force_login(self.bob)
        for url in (
            f'/api/simple-reports/{self.alice_report}/',
            f'/api/simple-reports/{self.alice_report}/export/?format=json',
            f'/api/simple-reports/{self.alice_report}/section-mappings/',
            f'/api/tasks/{self.alice_report}/',
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_owner_can_read_and_export(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(f'/api/simple-reports/{self.alice_report}/').status_code, 200)
        export = self.client.get(f'/api/simple-reports/{self.alice_report}/export/?format=json')
        self.assertEqual(export.status_code, 200)

    def test_unowned_reports_are_staff_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(f'/api/simple-reports/{self.unowned_report}/').status_code, 404)

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f'/api/simple-reports/{self.unowned_report}/').status_code, 200)
        listed = [r['id'] for r in self.client.get('/api/simple-reports/').json()['results']]
        self.assertIn(self.unowned_report, listed)

    def test_uploaded_report_is_owned_by_uploader(self):
        self.client.force_login(self.alice)
        with override_settings(OPENAI_API_KEY='sk-test'), patch('openai.OpenAI', side_effect=RuntimeError('boom')):
            response = self.client.post('/api/simple-upload/', {'file': _upload_file(), 'dataset_type': 'wacc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PersistedReport.objects.get(pk=response.json()['id']).owner, self.alice)


class CsrfTests(TestCase):
    def setUp(self):
        User.objects.create_user('analyst', password='Correct-horse-1')
        self.client = Client(enforce_csrf_checks=True)
        login = self.client.post(
            '/api/simple-login/',
            data=json.dumps({'username': 'analyst', 'password': 'Correct-horse-1'}),
            content_type='application/json',
        )
        self.assertEqual(login.status_code, 200)

    def test_post_without_csrf_token_is_rejected(self):
        self.assertEqual(self.client.post('/api/simple-upload/', {}).status_code, 403)

    def test_post_with_csrf_token_passes_csrf(self):
        token = self.client.cookies['csrftoken'].value
        response = self.client.post('/api/simple-upload/', {}, HTTP_X_CSRFTOKEN=token)
        # Past the CSRF check; fails validation instead because no dataset type or file was sent.
        self.assertEqual(response.status_code, 400)

    def test_vite_dev_origin_is_trusted(self):
        token = self.client.cookies['csrftoken'].value
        response = self.client.post(
            '/api/simple-upload/', {}, HTTP_X_CSRFTOKEN=token,
            HTTP_HOST='localhost:8000', HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(response.status_code, 400)

    def test_foreign_origin_is_rejected_even_with_token(self):
        token = self.client.cookies['csrftoken'].value
        response = self.client.post(
            '/api/simple-upload/', {}, HTTP_X_CSRFTOKEN=token,
            HTTP_HOST='localhost:8000', HTTP_ORIGIN='https://evil.example',
        )
        self.assertEqual(response.status_code, 403)


class LoginThrottleTests(TestCase):
    def setUp(self):
        User.objects.create_user('analyst', password='Correct-horse-1')

    def _login(self, password):
        return self.client.post(
            '/api/simple-login/',
            data=json.dumps({'username': 'analyst', 'password': password}),
            content_type='application/json',
        )

    @override_settings(LOGIN_MAX_FAILURES=3)
    def test_account_is_locked_after_repeated_failures(self):
        for _ in range(3):
            self.assertEqual(self._login('wrong').status_code, 401)
        locked = self._login('Correct-horse-1')
        self.assertEqual(locked.status_code, 429)
        self.assertIn('Retry-After', locked)

    @override_settings(LOGIN_MAX_FAILURES=3)
    def test_success_clears_earlier_failures(self):
        self._login('wrong')
        self._login('wrong')
        self.assertEqual(self._login('Correct-horse-1').status_code, 200)
        self.assertEqual(self._login('wrong').status_code, 401)
        self.assertEqual(self._login('wrong').status_code, 401)
        self.assertEqual(self._login('Correct-horse-1').status_code, 200)


class AIUsageLimitTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('uploader', password='Uploader-pass-1'))

    @override_settings(AI_REQUESTS_PER_USER_PER_HOUR=2, OPENAI_API_KEY='sk-test')
    @patch('openai.OpenAI', side_effect=RuntimeError('boom'))
    def test_per_user_hourly_limit(self, _openai):
        for _ in range(2):
            ok = self.client.post('/api/simple-upload/', {'file': _upload_file(), 'dataset_type': 'wacc'})
            self.assertEqual(ok.status_code, 200)
        blocked = self.client.post('/api/simple-upload/', {'file': _upload_file(), 'dataset_type': 'wacc'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('AI requests per hour', blocked.json()['error'])

    @override_settings(OPENAI_DAILY_QUOTA_LIMIT=1, OPENAI_API_KEY='sk-test')
    def test_daily_quota_stops_openai_calls(self):
        client = _fake_openai_client(_ai_reply())
        with patch('openai.OpenAI', return_value=client):
            first = generate_comprehensive_ai_analysis(_context())
            second = generate_comprehensive_ai_analysis(_context())
        self.assertTrue(first['ai_enhanced'])
        self.assertFalse(second['ai_enhanced'])
        self.assertIn('daily limit', second['error'])
        self.assertEqual(client.chat.completions.create.call_count, 1)


class ServerErrorDetailsTests(SimpleTestCase):
    def _run(self, status):
        middleware = HideServerErrorDetailsMiddleware(
            lambda request: JsonResponse({'error': r'C:\secret\path leaked'}, status=status)
        )
        return middleware(RequestFactory().get('/api/anything/'))

    @override_settings(DEBUG=False)
    def test_500_details_are_hidden_in_production(self):
        response = self._run(500)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret', response.content.decode())

    @override_settings(DEBUG=False)
    def test_other_statuses_keep_their_message(self):
        self.assertIn('secret', self._run(503).content.decode())

    @override_settings(DEBUG=True)
    def test_debug_mode_shows_details(self):
        self.assertIn('secret', self._run(500).content.decode())


class ReportStoreTests(TestCase):
    def test_reads_see_changes_made_by_other_processes(self):
        report_id = _save_report(owner=None)
        PersistedReport.objects.filter(pk=report_id).update(report_data={'id': report_id, 'status': 'changed'})
        self.assertEqual(get_report(report_id)['status'], 'changed')

    def test_saving_without_a_user_keeps_the_owner(self):
        owner = User.objects.create_user('owner', password='Owner-pass-1')
        report_id = _save_report(owner=owner)
        save_report(report_id, {'status': 'updated'})
        self.assertEqual(PersistedReport.objects.get(pk=report_id).owner, owner)


class SettingsSelectionTests(SimpleTestCase):
    def _select(self, env):
        from financial_analytics import env as env_module
        with patch.object(env_module, 'load_dotenv'), patch.dict(os.environ, env, clear=True):
            return env_module.configure_settings_module()

    def test_postgres_when_db_name_is_set(self):
        self.assertEqual(self._select({'DB_NAME': 'x'}), 'financial_analytics.settings_postgres')

    def test_sqlite_otherwise(self):
        self.assertEqual(self._select({}), 'financial_analytics.settings_sqlite')

    def test_explicit_settings_module_wins(self):
        chosen = self._select({'DB_NAME': 'x', 'DJANGO_SETTINGS_MODULE': 'financial_analytics.settings_sqlite'})
        self.assertEqual(chosen, 'financial_analytics.settings_sqlite')

    def test_wsgi_application_loads(self):
        from financial_analytics.wsgi import application
        self.assertTrue(callable(application))
