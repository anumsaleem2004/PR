from django.shortcuts import render, redirect
from django import forms
from django.contrib import messages
from github import Github, GithubException
from django.conf import settings
from .models import PullRequest
import ollama
import logging
from typing import Tuple, List, Optional
from dataclasses import dataclass
from django.shortcuts import render, redirect, get_object_or_404


logger = logging.getLogger(__name__)


@dataclass
class CodeReviewResult:
    is_approved: bool
    feedback: List[str]
    security_issues: List[str]
    suggested_changes: List[str]


class PRSubmissionForm(forms.Form):
    repo_url = forms.URLField(
        label='GitHub Repository URL',
        help_text='Full URL to the GitHub repository'
    )
    pr_link = forms.URLField(
        label='Pull Request Link',
        help_text='Full URL to the pull request'
    )


def analyze_code_with_llama(
        pr_diff: str,
        main_branch_code: str,
        file_changes: List[dict]
) -> CodeReviewResult:
    """
    Enhanced code analysis using Llama with structured output and specific checks.
    """
    try:
        # Create a more detailed prompt for better analysis
        prompt = f"""
        Perform a comprehensive code review focusing on:
        1. Security vulnerabilities
        2. Code quality and best practices
        3. Performance implications
        4. Breaking changes
        5. Test coverage

        Changes Overview:
        {', '.join(f"{change['file']} ({change['additions']} additions, {change['deletions']} deletions)"
                   for change in file_changes)}

        Detailed Changes:
        {pr_diff}

        Relevant Main Branch Context:
        {main_branch_code}

        Provide structured feedback in the following format:
        - SECURITY: List any security concerns
        - QUALITY: Code quality assessment
        - PERFORMANCE: Performance impact
        - BREAKING: Any breaking changes
        - RECOMMENDATION: Should this PR be merged? (YES/NO)
        """

        response = ollama.chat(
            model='llama3',
            messages=[{"role": "user", "content": prompt}],
            options={
                'temperature': 0.2,
                'top_p': 0.9,
                'max_tokens': 2000
            }
        )

        analysis = response['message']['content']

        # Parse the structured response
        is_approved = "RECOMMENDATION: YES" in analysis.upper()
        security_issues = [line.strip() for line in analysis.split('\n')
                           if line.strip().startswith('SECURITY:')]

        feedback = [
            section for section in analysis.split('\n')
            if any(section.startswith(prefix) for prefix in ['QUALITY:', 'PERFORMANCE:', 'BREAKING:'])
        ]

        suggested_changes = [
            line.strip() for line in analysis.split('\n')
            if line.strip().startswith('-') and not any(
                prefix in line for prefix in ['SECURITY:', 'QUALITY:', 'PERFORMANCE:', 'BREAKING:']
            )
        ]

        return CodeReviewResult(
            is_approved=is_approved,
            feedback=feedback,
            security_issues=security_issues,
            suggested_changes=suggested_changes
        )

    except Exception as e:
        logger.error(f"AI analysis failed: {str(e)}", exc_info=True)
        return CodeReviewResult(
            is_approved=False,
            feedback=["AI analysis failed - manual review required"],
            security_issues=["Unable to perform security analysis"],
            suggested_changes=[]
        )


def fetch_full_main_branch_code(repo, main_branch, pr_files: List[str]) -> Tuple[str, List[str]]:
    """
    Fetch main branch code with smart context fetching.
    Only fetches files that are related to the PR changes.
    """
    full_code = []
    errors = []

    try:
        # Get the directory structure of changed files
        changed_dirs = {'/'.join(file.split('/')[:-1]) for file in pr_files}

        # Fetch the tree with all files
        tree = repo.get_git_tree(main_branch.commit.sha, recursive=True).tree

        for file in tree:
            if file.type == 'blob':
                file_dir = '/'.join(file.path.split('/')[:-1])

                # Only fetch if the file is changed or in a changed directory
                if file.path in pr_files or file_dir in changed_dirs:
                    try:
                        file_content = repo.get_contents(file.path, ref=main_branch.name)
                        decoded_content = file_content.decoded_content.decode('utf-8', errors='ignore')
                        full_code.append(f"File: {file.path}\n{decoded_content}\n")
                    except Exception as e:
                        errors.append(f"Failed to fetch {file.path}: {str(e)}")

        return "\n".join(full_code), errors
    except Exception as e:
        logger.error(f"Failed to fetch main branch code: {str(e)}", exc_info=True)
        return "Failed to fetch main branch code.", [str(e)]


def check_merge_requirements(pr, repo) -> Tuple[bool, List[str]]:
    """
    Comprehensive merge requirement checks.
    """
    can_merge = True
    checks = []

    try:
        # Check if PR is already merged
        if pr.merged:
            return False, ["PR is already merged"]

        # Check if PR is mergeable
        if not pr.mergeable:
            return False, ["PR has conflicts that need to be resolved"]

        # Check required reviews
        reviews = pr.get_reviews()
        approved_reviews = sum(1 for review in reviews if review.state == 'APPROVED')
        required_reviews = 1  # Customize based on repository settings

        if approved_reviews < required_reviews:
            can_merge = False
            checks.append(f"Needs {required_reviews - approved_reviews} more approvals")

        # Check CI status
        commit = repo.get_commit(pr.head.sha)
        combined_status = commit.get_combined_status()

        if combined_status.state != 'success':
            can_merge = False
            checks.append(f"CI checks status: {combined_status.state}")

            # Add detailed status checks
            for status in combined_status.statuses:
                if status.state != 'success':
                    checks.append(f"- {status.context}: {status.state}")

        # Check branch protection rules
        try:
            branch = repo.get_branch(pr.base.ref)
            if branch.protected:
                protection = branch.get_protection()

                if protection.required_status_checks:
                    checks.append("Branch has protection rules enabled")
                    if not all(status.state == 'success' for status in combined_status.statuses):
                        can_merge = False
                        checks.append("Not all required status checks have passed")
        except GithubException:
            checks.append("Unable to check branch protection rules")

        return can_merge, checks

    except Exception as e:
        logger.error(f"Error checking merge requirements: {str(e)}", exc_info=True)
        return False, [f"Error checking merge requirements: {str(e)}"]


def submit_pr(request):
    if request.method == 'POST':
        form = PRSubmissionForm(request.POST)
        if form.is_valid():
            repo_url = form.cleaned_data['repo_url']
            pr_link = form.cleaned_data['pr_link']

            try:
                # Initialize GitHub connection
                g = Github(settings.GITHUB_TOKEN)
                repo_name = repo_url.split('github.com/')[-1].strip('/')
                pr_number = int(pr_link.split('/')[-1])

                repo = g.get_repo(repo_name)
                pr = repo.get_pull(pr_number)

                feedback = []
                status = "Pending"

                # Collect PR changes with detailed information
                file_changes = []
                changed_files = []
                for file in pr.get_files():
                    changed_files.append(file.filename)
                    file_changes.append({
                        'file': file.filename,
                        'additions': file.additions,
                        'deletions': file.deletions,
                        'changes': file.changes,
                        'status': file.status,
                        'patch': file.patch if file.patch else "Binary file"
                    })

                # Fetch relevant main branch code
                main_branch = repo.get_branch(repo.default_branch)
                main_branch_code, fetch_errors = fetch_full_main_branch_code(
                    repo, main_branch, changed_files
                )

                if fetch_errors:
                    feedback.extend([f"⚠️ {error}" for error in fetch_errors])

                # Check merge requirements
                can_merge, merge_checks = check_merge_requirements(pr, repo)
                feedback.extend(merge_checks)

                if not can_merge:
                    status = "Blocked"
                    feedback.append("❌ Cannot merge due to failing checks")
                else:
                    # Perform AI analysis
                    pr_diff = "\n".join(f"File: {change['file']}\n{change['patch']}"
                                        for change in file_changes)

                    review_result = analyze_code_with_llama(pr_diff, main_branch_code, file_changes)

                    # Add AI feedback to the review
                    feedback.extend([f"🤖 {item}" for item in review_result.feedback])

                    if review_result.security_issues:
                        feedback.extend([f"🚨 Security: {issue}"
                                         for issue in review_result.security_issues])

                    if review_result.suggested_changes:
                        feedback.extend([f"📝 Suggestion: {change}"
                                         for change in review_result.suggested_changes])

                    # Attempt merge if everything looks good
                    if review_result.is_approved and can_merge:
                        try:
                            pr.merge(
                                commit_message=f"Automatically merged by PR Agent (PR #{pr_number})\n\nAI Review: Approved",
                                merge_method='squash'  # or 'merge' or 'rebase' based on preference
                            )
                            status = "Approved"
                            feedback.append("✅ PR automatically merged successfully")
                        except Exception as e:
                            status = "Failed"
                            feedback.append(f"❌ Merge failed: {str(e)}")
                    else:
                        status = "Rejected"
                        feedback.append("❌ PR rejected based on review results")

                # Save review results
                PullRequest.objects.create(
                    repo_url=repo_url,
                    pr_link=pr_link,
                    status=status,
                    feedback="\n".join(feedback)
                )

                messages.success(
                    request,
                    "✅ PR review completed. Check feedback for details."
                )
                return redirect('pr_history')

            except Exception as e:
                logger.error(f"PR processing failed: {str(e)}", exc_info=True)
                messages.error(request, f"Processing failed: {str(e)}")
                return redirect('submit_pr')

    return render(request, 'pr_agent/create_pr.html', {'form': PRSubmissionForm()})


def pr_history(request):
    prs = PullRequest.objects.all().order_by('-created_at')
    return render(request, 'pr_agent/history.html', {'prs': prs})

def delete_pr(request, pr_id):
    pr = get_object_or_404(PullRequest, id=pr_id)
    pr.delete()
    return redirect('pr_history')  # Update 'pr_history' with the correct URL name