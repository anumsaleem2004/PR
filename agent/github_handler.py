import os
from datetime import datetime, timedelta
from typing import Tuple, List, Optional
from github import Github, GithubException, UnknownObjectException
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import PullRequest
import logging

logger = logging.getLogger(__name__)


class GitHubHandler:
    def __init__(self):
        self.github = Github(settings.GITHUB_TOKEN)
        self.high_risk_patterns = {
            'files': ['settings.py', 'config.py', 'requirements.txt', 'package.json',
                      'Dockerfile', 'docker-compose.yml', '.env'],
            'extensions': ['.sql', '.sh', '.env', '.yml', '.yaml']
        }

    def _extract_repo_info(self, repo_url: str) -> Tuple[str, str]:
        """Extract owner and repo name from GitHub URL."""
        try:
            url = repo_url.strip().rstrip('/')
            parts = url.split('/')
            owner = parts[-2]
            repo_name = parts[-1]
            return owner, repo_name
        except Exception as e:
            raise ValidationError(f"Invalid repository URL format: {str(e)}")

    def _extract_pr_number(self, pr_link: str) -> int:
        """Extract PR number from GitHub PR URL."""
        try:
            return int(pr_link.strip().rstrip('/').split('/')[-1])
        except Exception as e:
            raise ValidationError(f"Invalid pull request URL format: {str(e)}")

    def analyze_pull_request(self, repo_url: str, pr_link: str) -> PullRequest:
        """
        Analyze a GitHub pull request and create/update PullRequest record.

        Args:
            repo_url: GitHub repository URL
            pr_link: Pull request URL

        Returns:
            PullRequest: Updated or created PullRequest object

        Raises:
            ValidationError: If analysis fails
        """
        try:
            # Extract repository and PR information
            owner, repo_name = self._extract_repo_info(repo_url)
            pr_number = self._extract_pr_number(pr_link)

            # Get repository and PR objects from GitHub
            repo = self.github.get_repo(f"{owner}/{repo_name}")
            pr = repo.get_pull(pr_number)

            # Initialize feedback list
            feedback = []

            # Age analysis
            pr_age = datetime.now() - pr.created_at.replace(tzinfo=None)
            if pr_age > timedelta(days=30):
                feedback.append("🕒 PR is over 30 days old - consider updating with latest changes")

            # Size analysis
            files = list(pr.get_files())
            total_changes = sum(file.changes for file in files)

            if len(files) > 500:
                feedback.append("🚨 Very large PR: Over 500 files changed - consider breaking it down")
            elif len(files) > 200:
                feedback.append("⚠️ Large PR: Over 200 files changed - might need more thorough review")

            # Risk assessment
            high_risk_files = []
            for file in files:
                if any(risk_file in file.filename for risk_file in self.high_risk_patterns['files']):
                    high_risk_files.append(file.filename)
                if any(file.filename.endswith(ext) for ext in self.high_risk_patterns['extensions']):
                    high_risk_files.append(file.filename)

            if high_risk_files:
                feedback.append(f"🔒 High-risk files modified:\n" +
                                "\n".join(f"- {file}" for file in high_risk_files))

            # Description quality
            if not pr.body:
                feedback.append("📝 Missing PR description")
            elif len(pr.body.split()) < 20:
                feedback.append("📝 PR description is brief - consider adding more details")

            # Branch status
            base_branch = repo.get_branch(repo.default_branch)
            comparison = repo.compare(repo.default_branch, pr.head.ref)

            if comparison.behind_by > 0:
                feedback.append(f"⟲ Branch is {comparison.behind_by} commits behind {repo.default_branch}")

            # Review analysis
            reviews = list(pr.get_reviews())
            latest_reviews = {}

            for review in reviews:
                latest_reviews[review.user.login] = review.state

            approvals = sum(1 for state in latest_reviews.values() if state == 'APPROVED')
            changes_requested = sum(1 for state in latest_reviews.values() if state == 'CHANGES_REQUESTED')

            if changes_requested > 0:
                feedback.append(f"⚠️ Changes requested by {changes_requested} reviewer(s)")

            # CI status check
            commit_status = pr.get_commits().reversed[0].get_combined_status()
            if commit_status.state == 'pending':
                feedback.append("⏳ CI checks are still running")
            elif commit_status.state != 'success':
                feedback.append(f"❌ CI checks failed: {commit_status.state}")
                failed_checks = [
                    f"- {status.context}: {status.description}"
                    for status in commit_status.statuses
                    if status.state != 'success'
                ]
                feedback.extend(failed_checks)

            # Merge conflict check
            if pr.mergeable is False:
                feedback.append("⚠️ PR has merge conflicts that need to be resolved")

            # Determine overall status
            status = 'pending'
            if changes_requested > 0 or high_risk_files or pr.mergeable is False:
                status = 'rejected'
            elif (approvals >= 1 and
                  commit_status.state == 'success' and
                  not high_risk_files and
                  pr.mergeable):
                status = 'approved'

            # Create or update PR record
            pr_obj, created = PullRequest.objects.update_or_create(
                repo_url=repo_url,
                pr_link=pr_link,
                defaults={
                    'status': status,
                    'feedback': "\n".join(feedback) if feedback else "No issues found."
                }
            )

            return pr_obj

        except UnknownObjectException:
            raise ValidationError("Pull request or repository not found")
        except GithubException as e:
            if e.status == 403:
                raise ValidationError("API rate limit exceeded. Please try again later.")
            raise ValidationError(f"GitHub API error: {str(e)}")
        except Exception as e:
            logger.error(f"PR analysis error: {str(e)}")
            raise ValidationError(f"Error analyzing pull request: {str(e)}")

    def get_pr_status(self, pr_id: int) -> Tuple[str, List[str]]:
        """
        Get current status and feedback for a PR.

        Args:
            pr_id: PullRequest ID

        Returns:
            Tuple[str, List[str]]: Status and list of feedback items
        """
        try:
            pr = PullRequest.objects.get(id=pr_id)
            feedback_list = pr.feedback.split('\n') if pr.feedback else []
            return pr.status, feedback_list
        except PullRequest.DoesNotExist:
            raise ValidationError("Pull request not found")
        except Exception as e:
            logger.error(f"Error getting PR status: {str(e)}")
            raise ValidationError(f"Error retrieving pull request status: {str(e)}")

    def refresh_pr(self, pr_id: int) -> PullRequest:
        """
        Refresh analysis for an existing PR.

        Args:
            pr_id: PullRequest ID

        Returns:
            PullRequest: Updated PullRequest object
        """
        try:
            pr = PullRequest.objects.get(id=pr_id)
            return self.analyze_pull_request(pr.repo_url, pr.pr_link)
        except PullRequest.DoesNotExist:
            raise ValidationError("Pull request not found")
        except Exception as e:
            logger.error(f"Error refreshing PR: {str(e)}")
            raise ValidationError(f"Error refreshing pull request: {str(e)}")
