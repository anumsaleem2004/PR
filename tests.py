from django.test import TestCase, Client
from django.urls import reverse
from .models import Repository, PullRequest
from .github_handler import GitHubHandler
from unittest.mock import patch


class RepositoryModelTests(TestCase):
    def test_repository_str(self):
        repo = Repository.objects.create(
            name='test-repo',
            owner='test-owner',
            url='https://github.com/test-owner/test-repo'
        )
        self.assertEqual(str(repo), 'test-owner/test-repo')


class PullRequestModelTests(TestCase):
    def setUp(self):
        self.repo = Repository.objects.create(
            name='test-repo',
            owner='test-owner',
            url='https://github.com/test-owner/test-repo'
        )

    def test_pull_request_str(self):
        pr = PullRequest.objects.create(
            repository=self.repo,
            pr_number=1,
            title='Test PR',
            author='test-author',
            branch='feature/test'
        )
        self.assertEqual(str(pr), 'PR #1 - Test PR')


class GitHubHandlerTests(TestCase):
    def setUp(self):
        self.handler = GitHubHandler()

    def test_validate_repo_url_valid(self):
        url = 'https://github.com/owner/repo'
        owner, repo_name = self.handler.validate_repo_url(url)
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo_name, 'repo')

    def test_validate_repo_url_invalid(self):
        url = 'invalid-url'
        with self.assertRaises(ValueError):
            self.handler.validate_repo_url(url)


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.repo = Repository.objects.create(
            name='test-repo',
            owner='test-owner',
            url='https://github.com/test-owner/test-repo'
        )

    def test_connect_repository_get(self):
        response = self.client.get(reverse('pr_agent:connect'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pr_agent/connect.html')

    @patch('pr_agent.github_handler.GitHubHandler.validate_repo_url')
    @patch('pr_agent.github_handler.GitHubHandler.get_repository')
    def test_connect_repository_post(self, mock_get_repo, mock_validate):
        mock_validate.return_value = ('owner', 'repo')
        mock_get_repo.return_value = self.repo

        response = self.client.post(reverse('pr_agent:connect'), {
            'repo_url': 'https://github.com/owner/repo'
        })
        self.assertEqual(response.status_code, 302)