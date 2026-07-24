"""Forms for the portals app."""
import json

from django import forms

from .models import InputPortal, OutputPortal, Plugin, Resource, OutputSchema, DistributionRule


_TEXT = {'class': 'form-control'}
_SELECT = {'class': 'form-select'}
_TEXTAREA = {'class': 'form-control', 'rows': 3}
_TEXTAREA_CODE = {'class': 'form-control font-monospace', 'rows': 6}
_CHECKBOX = {'class': 'form-check-input'}
_NUMBER = {'class': 'form-control'}


class InputPortalForm(forms.ModelForm):
    class Meta:
        model = InputPortal
        fields = [
            'name', 'portal_type', 'stream_type', 'host', 'port',
            'username', 'password', 'directory', 'file_pattern',
            'polling_interval', 'is_active', 'description',
            # FAI / Protocol
            'script_name', 'connect_timeout', 'enable_transcript',
            'disposition', 'rename_ext', 'move_to', 'rmv_prefix', 'reg_pattern',
            # Dispatch
            'dispatch_status', 'dispatch_method', 'schedule_type', 'base_time',
            'interval_mins', 'do_not_dispatch_on', 'no_dispatch_intervals',
            'pending_processing_throttling', 'start_throttling_counts', 'end_throttling_counts',
            # Input Processing
            'duplicate_detection_algo', 'duplicate_disposition', 'duplicate_directory',
            'duplicate_business_logic', 'file_compression_type', 'input_script',
            'large_file_access_descriptor'
        ]
        help_texts = {
            'directory': (
                'File collection path — the directory to watch/poll for incoming '
                'CDR files. For the per-operator layout use '
                '{operator}/input/{vendor}/{ne} (e.g. orange/input/huawei/msc).'
            ),
            'file_pattern': 'Glob of files to collect, e.g. *.dat',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. SL_INP | HMSC'}),
            'portal_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'stream_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'host': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. localhost or 10.0.0.1'}),
            'port': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'Default depends on Protocol'}),
            'username': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. cgi'}),
            'password': forms.PasswordInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '*****'}, render_value=True),
            'directory': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '/rawfile_landing/INPUT/SL_INP.HMSC'}),
            'file_pattern': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '*.dat'}),
            'polling_interval': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'statusSwitch'}),
            'description': forms.Textarea(attrs={'class': 'form-control form-control-sm bg-light', 'rows': 2, 'placeholder': 'Brief description of the input portal'}),
            
            # FAI / Protocol
            'script_name': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'or_sftp_flex_inp.py'}),
            'connect_timeout': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'enable_transcript': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'disposition': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'rename_ext': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'move_to': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '/rawfile_landing/INPUT/SL_INP.HMSC/polled'}),
            'rmv_prefix': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'reg_pattern': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            
            # Dispatch
            'dispatch_status': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'dispatchStatusSwitch'}),
            'dispatch_method': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'schedule_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'base_time': forms.TimeInput(attrs={'class': 'form-control form-control-sm bg-light', 'type': 'time', 'step': '1'}),
            'interval_mins': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'do_not_dispatch_on': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'pending_processing_throttling': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'start_throttling_counts': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'end_throttling_counts': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),

            # Input Processing
            'duplicate_detection_algo': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('Disabled', 'Disabled'), ('Name', 'Name'), ('Size', 'Size'), ('Checksum', 'Checksum')]),
            'duplicate_disposition': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('Transfer and Process Normally', 'Transfer and Process Normally'), ('Delete', 'Delete'), ('Rename', 'Rename'), ('Move', 'Move')]),
            'duplicate_directory': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'duplicate_business_logic': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('None', 'None')]),
            'file_compression_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('No Compression', 'No Compression'), ('GZIP', 'GZIP'), ('ZIP', 'ZIP')]),
            'input_script': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('None', 'None')]),
            'large_file_access_descriptor': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}, choices=[('None', 'None')]),
        }


class OutputPortalForm(forms.ModelForm):
    class Meta:
        model = OutputPortal
        fields = [
            'name', 'portal_type', 'stream_type', 'output_format',
            'host', 'port', 'username', 'password', 'directory',
            'is_active', 'description',
        ]
        help_texts = {
            'directory': (
                'Output path. Absolute, or relative to the data dir. '
                'Placeholders: {operator} {vendor} {ne} {stream} {portal} '
                '{YYYY} {MM} {DD}. Leave blank to use the per-operator default '
                '{operator}/output/{vendor}/{ne}.'
            ),
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. Billing_SFTP'}),
            'portal_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'stream_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'output_format': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'host': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. 10.0.0.5'}),
            'port': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'username': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'password': forms.PasswordInput(attrs={'class': 'form-control form-control-sm bg-light'}, render_value=True),
            'directory': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '/incoming/MSC'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control form-control-sm bg-light', 'rows': 2}),
        }


class OutputSchemaForm(forms.ModelForm):
    mapping_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control form-control-sm bg-light font-monospace',
            'rows': 8,
            'placeholder': '{} for all fields, or {"MSISDN": "calling_number"}',
        }),
        help_text='JSON dict {output_column: source_field}. Empty {} = dump all model fields.',
    )

    class Meta:
        model = OutputSchema
        fields = ['name', 'stream_type', 'mapping_json', 'delimiter', 'include_header', 'quote_all', 'line_terminator']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. Billing Mapping'}),
            'stream_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'mapping_json': forms.Textarea(attrs={'class': 'form-control form-control-sm bg-light font-monospace', 'rows': 8, 'placeholder': '{"MSISDN": "subscriber_id", "Start_Time": "start_time"}'}),
            'delimiter': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': ','}),
            'include_header': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'quote_all': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'line_terminator': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '\\n'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and isinstance(self.instance.mapping_json, dict):
            self.initial['mapping_json'] = json.dumps(self.instance.mapping_json, indent=2)
        # Default new schemas to MSC instead of ALL so visual editor populates
        if not (self.instance and self.instance.pk):
            self.fields['stream_type'].initial = 'MSC'

    def clean_mapping_json(self):
        raw = self.cleaned_data.get('mapping_json')
        if isinstance(raw, dict):
            return raw
        if not raw or not str(raw).strip():
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc
        if not isinstance(data, dict):
            raise forms.ValidationError('Mapping must be a JSON object {output_column: source_field}.')
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise forms.ValidationError('All keys and values must be strings.')
        return data


class DistributionRuleForm(forms.ModelForm):
    class Meta:
        model = DistributionRule
        fields = ['name', 'stream_type', 'output_portal', 'output_schema', 'filter_logic',
                  'is_active', 'priority', 'max_retries', 'retry_backoff_seconds']
        help_texts = {
            'filter_logic': 'JSON dict applied via Django ORM .filter(**kwargs). Empty matches all records.',
            'max_retries': 'Total delivery attempts before giving up (1 = no retry).',
            'retry_backoff_seconds': 'Comma-separated wait seconds between attempts, e.g. "2,5,10".',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': 'e.g. MSC to Billing Rule'}),
            'stream_type': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'output_portal': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'output_schema': forms.Select(attrs={'class': 'form-select form-select-sm bg-light'}),
            'filter_logic': forms.Textarea(attrs={'class': 'form-control form-control-sm bg-light font-monospace', 'rows': 3, 'placeholder': '{"prepaid_flag": "POSTPAID", "is_roaming": false}'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'priority': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light'}),
            'max_retries': forms.NumberInput(attrs={'class': 'form-control form-control-sm bg-light', 'min': 1, 'max': 10}),
            'retry_backoff_seconds': forms.TextInput(attrs={'class': 'form-control form-control-sm bg-light', 'placeholder': '2,5'}),
        }

    def clean_filter_logic(self):
        raw = (self.cleaned_data.get('filter_logic') or '').strip()
        if not raw:
            return ''
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc
        if not isinstance(data, dict):
            raise forms.ValidationError('filter_logic must be a JSON object')
        return raw


class PluginForm(forms.ModelForm):
    class Meta:
        model = Plugin
        fields = [
            'name', 'plugin_type', 'version', 'is_active',
            'author', 'description', 'config',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_TEXT),
            'plugin_type': forms.Select(attrs=_SELECT),
            'version': forms.TextInput(attrs=_TEXT),
            'is_active': forms.CheckboxInput(attrs=_CHECKBOX),
            'author': forms.TextInput(attrs=_TEXT),
            'description': forms.Textarea(attrs=_TEXTAREA),
            'config': forms.Textarea(attrs=_TEXTAREA_CODE),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display JSON config as formatted text
        if self.instance and self.instance.pk and self.instance.config:
            self.initial['config'] = json.dumps(self.instance.config, indent=2)

    def clean_config(self):
        raw = self.cleaned_data.get('config', '')
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc


class ResourceForm(forms.ModelForm):
    class Meta:
        model = Resource
        fields = [
            'name', 'resource_type', 'status', 'host',
            'is_monitored', 'description', 'config',
        ]
        widgets = {
            'name': forms.TextInput(attrs=_TEXT),
            'resource_type': forms.Select(attrs=_SELECT),
            'status': forms.Select(attrs=_SELECT),
            'host': forms.TextInput(attrs=_TEXT),
            'is_monitored': forms.CheckboxInput(attrs=_CHECKBOX),
            'description': forms.Textarea(attrs=_TEXTAREA),
            'config': forms.Textarea(attrs=_TEXTAREA_CODE),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.config:
            self.initial['config'] = json.dumps(self.instance.config, indent=2)

    def clean_config(self):
        raw = self.cleaned_data.get('config', '')
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc
