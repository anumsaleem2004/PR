# Generated by Django 5.0.2 on 2025-02-22 11:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0002_analysisfeedback_analysismetrics_pranalysis_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='UploadedFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('file_path', models.CharField(max_length=500)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
