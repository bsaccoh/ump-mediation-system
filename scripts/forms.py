from django import forms
from .models import Script


class ScriptForm(forms.ModelForm):
    class Meta:
        model = Script
        fields = ['name', 'category', 'script_type', 'status', 'description', 'content', 'tags']
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. daily_cleanup'}),
            'category':    forms.Select(attrs={'class': 'form-select'}),
            'script_type': forms.Select(attrs={'class': 'form-select'}),
            'status':      forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Brief description of what this script does…'}),
            'content':     forms.Textarea(attrs={
                'class': 'form-control font-monospace',
                'rows': 20,
                'spellcheck': 'false',
                'autocorrect': 'off',
                'autocapitalize': 'off',
                'placeholder': '# Write your script here…',
                'style': 'font-size:0.85rem; line-height:1.5; tab-size:4;',
            }),
            'tags': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. etl, cleanup, report'}),
        }
