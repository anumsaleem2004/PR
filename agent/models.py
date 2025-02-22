from django.db import models

class PullRequest(models.Model):
    repo_url = models.URLField()
    pr_link = models.URLField()
    status = models.CharField(max_length=20)  # Approved, Rejected, Pending
    feedback = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)