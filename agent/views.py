from django.shortcuts import render, redirect, get_object_or_404
from django import forms
from django.contrib import messages
from github import Github, GithubException
from django.conf import settings
from .models import PullRequest
import ollama
import logging
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import pytz
import time
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


@dataclass
class CodeReviewResult:
    is_approved: bool
    feedback: List[str]
    security_issues: List[str]
    suggested_changes: List[str]
    code_quality_score: float
    breaking_changes: List[str]
    test_coverage_analysis: Dict[str, any]


class PRSubmissionForm(forms.Form):
    repo_url = forms.URLField(
        label='GitHub Repository URL',
        help_text='Full URL to the GitHub repository'
    )
    pr_link = forms.URLField(
        label='Pull Request Link',
        help_text='Full URL to the pull request'
    )


def fetch_pr_details(repo, pr_number: int) -> Tuple[Optional[object], List[str], str]:
    try:
        pr = repo.get_pull(pr_number)
        messages = []
        status = "Pending"

        messages.append(f"PR Title: {pr.title}")
        messages.append(f"Author: {pr.user.login}")
        messages.append(f"Created: {pr.created_at}")
        messages.append(f"Last Updated: {pr.updated_at}")

        if pr.state == "closed" and not pr.merged:
            return None, ["PR is already closed without being merged"], "Closed"
        elif pr.merged:
            return None, ["PR is already merged"], "Merged"

        return pr, messages, status
    except Exception as e:
        logger.error(f"Failed to fetch PR details: {str(e)}", exc_info=True)
        return None, [f"Failed to fetch PR: {str(e)}"], "Failed"


def analyze_code_changes(pr_files: List[dict], main_branch_code: str) -> Dict[str, any]:
    analysis = {
        'high_risk_files': [],
        'complexity_score': 0,
        'impact_level': 'low',
        'test_coverage': {},
        'affected_components': set()
    }

    for file in pr_files:
        path_parts = file['file'].split('/')
        if len(path_parts) > 1:
            analysis['affected_components'].add(path_parts[0])

        if any(risk_pattern in file['file'].lower() for risk_pattern in
               ['security', 'auth', 'password', 'crypto', 'payment']):
            analysis['high_risk_files'].append(file['file'])

        complexity = (file['additions'] + file['deletions']) * 0.1
        if file['changes'] > 100:
            complexity *= 1.5
        analysis['complexity_score'] += complexity

        if 'test' in file['file'].lower():
            covered_file = file['file'].replace('test_', '').replace('tests/', '')
            analysis['test_coverage'][covered_file] = True

    if analysis['complexity_score'] > 50 or len(analysis['high_risk_files']) > 0:
        analysis['impact_level'] = 'high'
    elif analysis['complexity_score'] > 20:
        analysis['impact_level'] = 'medium'

    return analysis


def analyze_code_with_llama(
        pr_diff: str,
        main_branch_code: str,
        file_changes: List[dict],
        pr_metadata: Dict[str, any]
) -> CodeReviewResult:
    try:
        models_to_try = [
            'llama2:7b-code',
            'codellama:7b',
            'mistral:7b',
            'llama2:7b'
        ]

        response = None
        for model in models_to_try:
            try:
                prompt = f"""
                Review this code change:
                Title: {pr_metadata.get('title')}
                Author: {pr_metadata.get('author')}

                Files changed:
                {', '.join(f"{change['file']}" for change in file_changes[:3])}

                Key changes:
                {pr_diff[:1000]}

                Provide concise analysis:
                SECURITY:
                QUALITY:
                PERFORMANCE:
                BREAKING:
                TESTS:
                RECOMMEND:
                """

                response = ollama.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    options={'temperature': 0.2, 'top_p': 0.9, 'max_tokens': 1000}
                )
                break
            except Exception:
                continue

        if not response:
            return perform_basic_analysis(file_changes, pr_metadata)

        analysis = response['message']['content']
        sections = {
            'security': [],
            'quality': [],
            'performance': [],
            'breaking': [],
            'testing': [],
        }

        current_section = None
        for line in analysis.split('\n'):
            line = line.strip()
            if not line:
                continue

            line_upper = line.upper()
            if 'SECURITY:' in line_upper:
                current_section = 'security'
            elif 'QUALITY:' in line_upper:
                current_section = 'quality'
            elif 'PERFORMANCE:' in line_upper:
                current_section = 'performance'
            elif 'BREAKING:' in line_upper:
                current_section = 'breaking'
            elif 'TESTS:' in line_upper:
                current_section = 'testing'
            elif current_section:
                sections[current_section].append(line)

        code_quality_score = calculate_basic_score(file_changes, sections)

        return CodeReviewResult(
            is_approved="RECOMMEND: YES" in analysis.upper() or "RECOMMEND: APPROVE" in analysis.upper(),
            feedback=sections['quality'] + sections['performance'],
            security_issues=sections['security'],
            suggested_changes=[s for s in sections['quality'] if 'suggest' in s.lower()],
            code_quality_score=code_quality_score,
            breaking_changes=sections['breaking'],
            test_coverage_analysis={'findings': sections['testing']}
        )

    except Exception as e:
        logger.error("Critical error in code analysis", exc_info=True)
        return perform_basic_analysis(file_changes, pr_metadata)


def perform_basic_analysis(file_changes: List[dict], pr_metadata: Dict[str, any]) -> CodeReviewResult:
    feedback = []
    security_issues = []
    breaking_changes = []

    total_changes = sum(f['changes'] for f in file_changes)
    test_files = [f for f in file_changes if 'test' in f['file'].lower()]

    if total_changes > 500:
        feedback.append("Large change set detected - consider breaking into smaller PRs")

    test_ratio = len(test_files) / len(file_changes) if file_changes else 0
    if test_ratio < 0.1:
        feedback.append("Low test coverage detected - consider adding more tests")

    security_patterns = ['password', 'token', 'secret', 'auth', 'credential']
    for file in file_changes:
        if any(pattern in file['file'].lower() for pattern in security_patterns):
            security_issues.append(f"Security-sensitive file modified: {file['file']}")

    base_score = 7.0
    if total_changes > 500:
        base_score -= 1.0
    if test_ratio < 0.1:
        base_score -= 1.0
    if security_issues:
        base_score -= 1.0 * len(security_issues)

    return CodeReviewResult(
        is_approved=base_score >= 7.0 and not security_issues,
        feedback=feedback,
        security_issues=security_issues,
        suggested_changes=[],
        code_quality_score=max(0.0, min(10.0, base_score)),
        breaking_changes=breaking_changes,
        test_coverage_analysis={'test_ratio': test_ratio}
    )


def calculate_basic_score(file_changes: List[dict], sections: Dict[str, List[str]]) -> float:
    score = 8.0
    score -= len(sections['security']) * 1.5
    score -= len(sections['breaking']) * 1.0
    score -= len([i for i in sections['quality'] if 'critical' in i.lower()]) * 2.0

    total_changes = sum(f['changes'] for f in file_changes)
    if total_changes > 500:
        score -= 1.0

    return max(0.0, min(10.0, score))


def create_unique_backup_ref(repo, pr_number: int, commit_sha: str) -> Tuple[str, List[str]]:
    feedback = []
    timestamp = datetime.now(pytz.UTC).strftime('%Y%m%d-%H%M%S')

    backup_ref_templates = [
        f"refs/backup/pr-{pr_number}-{commit_sha[:7]}",
        f"refs/backup/pr-{pr_number}-{commit_sha[:7]}-{timestamp}",
        f"refs/backup/pr-{pr_number}-{commit_sha}-{timestamp}"
    ]

    for ref_name in backup_ref_templates:
        try:
            try:
                repo.get_git_ref(ref_name)
                feedback.append(f"⚠️ Backup reference {ref_name} already exists, trying alternative...")
                continue
            except GithubException:
                repo.create_git_ref(ref_name, commit_sha)
                feedback.append(f"✅ Created backup reference: {ref_name}")
                return ref_name, feedback
        except GithubException as e:
            if "Reference already exists" in str(e):
                continue
            feedback.append(f"⚠️ Failed to create backup reference {ref_name}: {str(e)}")

    return backup_ref_templates[-1], feedback


def handle_pr_merge(pr, review_result, change_analysis, feedback_list):
    try:
        # Check if PR can be merged
        if not pr.mergeable:
            return False, ["❌ PR has merge conflicts that need to be resolved"], "Conflict"

        repo = pr.base.repo
        
        # Create backup before any operations
        backup_ref, backup_feedback = create_unique_backup_ref(repo, pr.number, pr.head.sha)
        feedback_list.extend(backup_feedback)

        # First, get latest changes from base branch
        try:
            base_branch = repo.get_branch(pr.base.ref)
            base_sha = base_branch.commit.sha
            
            # Update PR branch with latest base branch changes to prevent conflicts
            pr.update_branch(expected_head_sha=pr.head.sha)
            feedback_list.append(f"✅ Synced PR branch with latest {pr.base.ref}")
            
            # Wait for GitHub to process the update
            time.sleep(5)
            
            # Refresh PR object to get latest state
            pr = repo.get_pull(pr.number)
            
        except GithubException as e:
            if e.status == 422:  # Unprocessable Entity
                feedback_list.append("ℹ️ PR branch is already up to date")
            else:
                return False, [f"❌ Failed to sync PR branch: {str(e)}"], "Failed"

        # Re-check mergeability after sync
        if not pr.mergeable:
            return False, ["❌ Conflicts detected after syncing with base branch"], "Conflict"

        # Simplified automatic merge criteria
        can_merge = (
            pr.mergeable and
            not pr.draft and
            review_result.code_quality_score >= 5.0 and  # Basic quality threshold
            not any(  # Block only on critical security issues
                'critical' in issue.lower() or 
                'severe' in issue.lower() 
                for issue in review_result.security_issues
            )
        )

        if not can_merge:
            reasons = []
            if not pr.mergeable:
                reasons.append("PR has conflicts")
            if pr.draft:
                reasons.append("PR is in draft state")
            if review_result.code_quality_score < 5.0:
                reasons.append("Code quality score too low")
            if any('critical' in issue.lower() or 'severe' in issue.lower() 
                  for issue in review_result.security_issues):
                reasons.append("Critical security issues found")

            return False, [f"❌ Cannot auto-merge: {', '.join(reasons)}"], "Rejected"

        # Attempt to merge PR into base branch
        merge_commit_message = f"""
Automated merge of PR #{pr.number}: {pr.title}

Branch: {pr.head.ref} → {pr.base.ref}
Backup Reference: {backup_ref}

AI Review Summary:
- Quality Score: {review_result.code_quality_score}/10
- Security Status: {'🔒 No critical issues' if not review_result.security_issues else '⚠️ Non-critical issues noted'}
- Breaking Changes: {'None detected' if not review_result.breaking_changes else 'Present'}

Review Details:
{chr(10).join(f'- {feedback}' for feedback in review_result.feedback[:5])}

Note: This PR was automatically merged after passing automated checks.
        """.strip()

        try:
            # Merge PR into base branch (e.g., main)
            merge_result = pr.merge(
                commit_title=f"Auto-merge PR #{pr.number}: {pr.title}",
                commit_message=merge_commit_message,
                merge_method='merge',  # Use merge commit to preserve PR history
                sha=pr.head.sha  # Ensure we're merging the specific commit we reviewed
            )

            if merge_result.merged:
                feedback_list.append(f"✅ Successfully merged {pr.head.ref} into {pr.base.ref}")
                
                # Clean up backup if merge successful
                try:
                    if backup_ref.startswith('refs/'):
                        backup_ref = backup_ref[5:]  # Remove 'refs/' prefix
                    repo.get_git_ref(backup_ref).delete()
                    feedback_list.append("✅ Cleaned up backup reference")
                except Exception as e:
                    feedback_list.append(f"⚠️ Failed to clean up backup reference: {str(e)}")
                
                return True, ["✅ PR successfully merged"], "Merged"
            else:
                return False, ["❌ Merge failed: " + (merge_result.message or "Unknown error")], "Failed"
                
        except GithubException as e:
            error_message = str(e)
            if "required status checks" in error_message.lower():
                return False, ["❌ Cannot merge: Required status checks are pending"], "Pending"
            elif "review" in error_message.lower():
                return False, ["❌ Cannot merge: Required reviews are missing"], "ReviewNeeded"
            else:
                return False, [f"❌ Merge failed: {error_message}"], "Failed"

    except Exception as e:
        logger.error(f"Merge process failed: {str(e)}", exc_info=True)
        return False, [f"❌ Merge process failed: {str(e)}"], "Failed"
def submit_pr(request):
    if request.method == 'POST':
        form = PRSubmissionForm(request.POST)
        if form.is_valid():
            try:
                g = Github(settings.GITHUB_TOKEN)
                repo_name = form.cleaned_data['repo_url'].split('github.com/')[-1].strip('/')
                pr_number = int(form.cleaned_data['pr_link'].split('/')[-1])
                repo = g.get_repo(repo_name)

                pr, initial_messages, status = fetch_pr_details(repo, pr_number)
                if not pr:
                    messages.error(request, initial_messages[0])
                    return redirect('submit_pr')

                file_changes = [{
                    'file': f.filename,
                    'additions': f.additions,
                    'deletions': f.deletions,
                    'changes': f.changes,
                    'status': f.status,
                    'patch': f.patch or "Binary file"
                } for f in pr.get_files()]

                change_analysis = analyze_code_changes(file_changes, "")
                pr_metadata = {
                    'title': pr.title,
                    'author': pr.user.login,
                    'components': list(change_analysis['affected_components'])
                }

                # Run automated review
                review_result = analyze_code_with_llama(
                    "\n".join(f"File: {c['file']}\n{c['patch']}" for c in file_changes),
                    "",
                    file_changes,
                    pr_metadata
                )

                feedback = initial_messages
                feedback.extend(f"🔍 {item}" for item in review_result.feedback)
                
                if review_result.security_issues:
                    feedback.extend(["🚨 Security Issues:"] + 
                                 [f"  - {issue}" for issue in review_result.security_issues])
                
                if review_result.breaking_changes:
                    feedback.extend(["⚠️ Breaking Changes:"] + 
                                 [f"  - {change}" for change in review_result.breaking_changes])
                
                feedback.append(f"📊 Code Quality Score: {review_result.code_quality_score}/10")

                # Attempt automatic merge
                merge_success, merge_feedback, status = handle_pr_merge(
                    pr, review_result, change_analysis, feedback
                )
                feedback.extend(merge_feedback)

                # Save PR record
                PullRequest.objects.create(
                    repo_url=form.cleaned_data['repo_url'],
                    pr_link=form.cleaned_data['pr_link'],
                    status=status,
                    feedback="\n".join(feedback)
                )

                if merge_success:
                    messages.success(request, "✅ PR successfully auto-merged")
                else:
                    messages.warning(request, "PR review completed but auto-merge failed")
                return redirect('pr_history')

            except Exception as e:
                logger.error(f"PR processing failed: {str(e)}", exc_info=True)
                messages.error(request, f"Processing failed: {str(e)}")

    return render(request, 'pr_agent/create_pr.html', {'form': PRSubmissionForm()})

def pr_history(request):
    return render(request, 'pr_agent/history.html', {
        'prs': PullRequest.objects.all().order_by('-created_at')
    })


def delete_pr(request, pr_id):
    pr = get_object_or_404(PullRequest, id=pr_id)
    pr.delete()
    return redirect('pr_history')
