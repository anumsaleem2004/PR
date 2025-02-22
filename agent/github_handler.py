import os
from github import Github
from django.conf import settings
from .models import Repository, PullRequest


class GitHubHandler:
    def __init__(self):
        self.github = Github(settings.GITHUB_TOKEN)

    def validate_repo_url(self, url):
        """Validate and extract repository information from URL."""
        try:
            # Remove trailing slash if present
            url = url.rstrip('/')
            parts = url.split('/')
            owner = parts[-2]
            repo_name = parts[-1]
            return owner, repo_name
        except Exception:
            raise ValueError("Invalid repository URL format")

    def get_repository(self, owner, repo_name):
        """Fetch repository information and create/update local record."""
        try:
            repo = self.github.get_repo(f"{owner}/{repo_name}")
            repo_obj, created = Repository.objects.get_or_create(
                owner=owner,
                name=repo_name,
                defaults={
                    'url': f"https://github.com/{owner}/{repo_name}"
                }
            )
            return repo_obj
        except Exception as e:
            raise ValueError(f"Error accessing repository: {str(e)}")

    def get_pull_request(self, repo_id, pr_number):
        """Fetch pull request information and create local record."""
        try:
            repository = Repository.objects.get(id=repo_id)
            repo = self.github.get_repo(f"{repository.owner}/{repository.name}")
            pr = repo.get_pull(pr_number)

            pr_obj, created = PullRequest.objects.get_or_create(
                repository=repository,
                pr_number=pr_number,
                defaults={
                    'title': pr.title,
                    'description': pr.body or '',
                    'author': pr.user.login,
                    'branch': pr.head.ref,
                    'status': 'pending'
                }
            )
            return pr_obj
        except Exception as e:
            raise ValueError(f"Error accessing pull request: {str(e)}")

    def analyze_pr(self, pr_id):
        """Analyze pull request and update local record with results."""
        try:
            pr = PullRequest.objects.get(id=pr_id)
            repo = self.github.get_repo(f"{pr.repository.owner}/{pr.repository.name}")
            github_pr = repo.get_pull(pr.pr_number)

            comments = []

            # Size check
            if github_pr.changed_files > 500:
                comments.append("⚠️ Large PR: Contains more than 500 file changes")
            elif github_pr.changed_files > 200:
                comments.append("⚠️ Medium-sized PR: Consider breaking down if possible")

            # Description check
            if not github_pr.body:
                comments.append("⚠️ Missing PR description")
            elif len(github_pr.body.split()) < 10:
                comments.append("⚠️ PR description is too brief")

            # Merge conflict check
            if not github_pr.mergeable:
                comments.append("⚠️ PR has merge conflicts that need to be resolved")

            # Review status check
            reviews = github_pr.get_reviews()
            has_approvals = any(review.state == 'APPROVED' for review in reviews)
            has_changes_requested = any(review.state == 'CHANGES_REQUESTED' for review in reviews)

            if has_changes_requested:
                comments.append("⚠️ Changes have been requested by reviewers")
            elif not has_approvals:
                comments.append("ℹ️ Waiting for review approvals")

            # Update PR status
            new_status = 'changes_requested' if comments else 'approved'
            if has_changes_requested:
                new_status = 'changes_requested'
            elif not has_approvals:
                new_status = 'pending'

            # Save analysis results
            pr.review_comments = "\n".join(comments)
            pr.status = new_status
            pr.save()

            return pr
        except Exception as e:
            raise ValueError(f"Error analyzing pull request: {str(e)}")