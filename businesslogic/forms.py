from django import forms
from .models import BusinessRule


class BusinessRuleForm(forms.ModelForm):
    class Meta:
        model = BusinessRule
        fields = ['name', 'rule_type', 'stream', 'status', 'priority',
                  'description', 'condition', 'action', 'tags']
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'form-control'}),
            'rule_type':   forms.Select(attrs={'class': 'form-select'}),
            'stream':      forms.Select(attrs={'class': 'form-select'}),
            'status':      forms.Select(attrs={'class': 'form-select'}),
            'priority':    forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'condition':   forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 6,
                                                  'placeholder': '{"field": "duration", "operator": ">", "value": 0}'}),
            'action':      forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 6,
                                                  'placeholder': '{"action": "reject", "reason": "Zero duration call"}'}),
            'tags':        forms.TextInput(attrs={'class': 'form-control',
                                                   'placeholder': 'roaming, validation, critical'}),
        }
