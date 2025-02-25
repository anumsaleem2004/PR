# PR Analysis Tool

An AI-powered Pull Request analysis and auto-merge tool for GitHub repositories.

## Overview

This Django application automates the code review process for GitHub Pull Requests by:

1. Fetching complete file data from PRs
2. Analyzing code changes using LLM (Ollama models)
3. Providing quality assessment and security reviews
4. Auto-merging PRs that meet quality criteria

## Features

- **Comprehensive Code Analysis**: Fetches and analyzes both the full file content and specific diffs
- **Security Scanning**: Identifies potential security issues in changed files
- **Quality Scoring**: Rates code quality on a 10-point scale
- **Safe Auto-Merge**: Creates backup references before attempting to merge
- **Breaking Change Detection**: Identifies potential breaking changes
- **Test Coverage Analysis**: Evaluates the presence of tests related to changes

## Requirements

- Python 3.8+
- Django 3.2+
- PyGithub
- Ollama (with supported models)

## Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Configure your GitHub token in settings:
   ```python
   # settings.py
   GITHUB_TOKEN = 'your_github_token'
   ```
4. Run migrations: `python manage.py migrate`
5. Start the server: `python manage.py runserver`

## Usage

1. Navigate to the homepage
2. Enter the GitHub repository URL and Pull Request link
3. Submit the form to start the analysis
4. Review the automated feedback and merge status

## Supported Models

The tool attempts to use the following models in order:
- llama2
- llama2:7b-code
- codellama:7b
- mistral:7b
- llama2:7b

## Configuration

You can adjust the analysis thresholds in the code:
- Quality score threshold for auto-merge: 5.0
- Security issues that block merging: "critical" or "severe"

## Architecture

- **fetch_pr_details**: Retrieves basic PR information
- **fetch_file_content**: Gets full content of files from both branches
- **analyze_code_changes**: Performs basic static analysis of changes
- **analyze_code_with_llama**: Uses LLM to analyze code quality and issues
- **handle_pr_merge**: Safely attempts to merge qualifying PRs

## Safety Features

- Creates backup references before any merge operation
- Syncs PR with base branch to prevent merge conflicts
- Validates mergeability before attempting merge
- Comprehensive error handling and logging

## License

MIT License

## Contributing

Contributions welcome! Please feel free to submit a Pull Request.
