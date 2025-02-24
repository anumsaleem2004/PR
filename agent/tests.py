from django.test import TestCase

# Create your tests here.
from django.test import TestCase
from .forms import PRSubmissionForm
# Create your tests here.
# tests.py
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.messages import get_messages
from .models import PullRequest
from unittest.mock import patch

class PRViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.submit_url = reverse('submit_pr')
        self.history_url = reverse('pr_history')
        self.test_repo = 'test/repo'
        self.test_pr_link = 'http://github.com/test/repo/pull/1'

        self.valid_form_data = {
            'repo_url': f'https://github.com/{self.test_repo}',
            'pr_link': self.test_pr_link
        }

    def test_submit_pr_get(self):
        response = self.client.get(self.submit_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pr_agent/create_pr.html')

    @patch('github.Github.get_repo')
    @patch('pr_agent.views.fetch_pr_details')
    def test_submit_pr_valid_form(self, mock_fetch, mock_repo):
        mock_fetch.return_value = (None, ['Mocked PR'], 'Mocked')
        mock_repo.return_value = None

        response = self.client.post(self.submit_url, data=self.valid_form_data)
        self.assertRedirects(response, reverse('pr_history'))

    def test_submit_pr_invalid_form(self):
        invalid_data = self.valid_form_data.copy()
        invalid_data['repo_url'] = 'invalid-url'
        response = self.client.post(self.submit_url, data=invalid_data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'repo_url', 'Enter a valid URL.')

    @patch('github.Github.get_repo')
    def test_submit_pr_github_error(self, mock_repo):
        mock_repo.side_effect = Exception('API Error')
        response = self.client.post(self.submit_url, data=self.valid_form_data)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any('Processing failed' in str(m) for m in messages))

    def test_pr_history_view(self):
        PullRequest.objects.create(
            repo_url=self.test_repo,
            pr_link=self.test_pr_link,
            status='Open',
            feedback='Test feedback'
        )
        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.test_repo)

    def test_delete_pr(self):
        pr = PullRequest.objects.create(
            repo_url=self.test_repo,
            pr_link=self.test_pr_link,
            status='Open'
        )
        delete_url = reverse('delete_pr', args=[pr.id])
        response = self.client.get(delete_url)
        self.assertRedirects(response, reverse('pr_history'))
        self.assertEqual(PullRequest.objects.count(), 0)

    @patch('pr_agent.views.handle_pr_merge')
    @patch('pr_agent.views.analyze_code_with_llama')
    def test_full_merge_process(self, mock_analyze, mock_merge):
        mock_analyze.return_value = MagicMock(
            is_approved=True,
            code_quality_score=8.0,
            security_issues=[]
        )
        mock_merge.return_value = (True, ['Merged'], 'Success')

        response = self.client.post(self.submit_url, data=self.valid_form_data)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any('successfully reviewed' in str(m) for m in messages))

class ModelTests(TestCase):
    def test_pr_creation(self):
        pr = PullRequest.objects.create(
            repo_url='test/repo',
            pr_link='http://example.com/pr/1',
            status='Open',
            feedback='Test feedback'
        )
        self.assertEqual(str(pr), f'PR {pr.id} - test/repo')

class FormTests(TestCase):
    def test_valid_form(self):
        form_data = {
            'repo_url': 'https://github.com/user/repo',
            'pr_link': 'https://github.com/user/repo/pull/1'
        }
        form = PRSubmissionForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_invalid_urls(self):
        form_data = {
            'repo_url': 'not-a-url',
            'pr_link': 'not-a-url'
        }
        form = PRSubmissionForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertEqual(len(form.errors), 2)
