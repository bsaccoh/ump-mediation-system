from django import forms

from drive_test.models import Campaign, Project
from drive_test.models.enums import Technology


class _BootstrapMixin:
    """Apply consistent form-control styling to every widget."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            css = w.attrs.get('class', '')
            if isinstance(w, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            if isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs['class'] = (css + ' form-select form-select-sm').strip()
            else:
                w.attrs['class'] = (css + ' form-control form-control-sm').strip()


class ProjectForm(_BootstrapMixin, forms.ModelForm):
    technologies = forms.MultipleChoiceField(
        choices=Technology.choices, required=False,
        widget=forms.SelectMultiple(attrs={'size': 4}),
        help_text='Technologies under test.',
    )

    class Meta:
        model = Project
        fields = [
            'name', 'description', 'start_date', 'end_date',
            'region', 'district', 'operators', 'technologies', 'status',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class CampaignForm(_BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Campaign
        fields = [
            'name', 'operator', 'technology', 'start_date', 'end_date',
            'device', 'tester', 'region', 'district', 'description',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }
