"""
Scripts Views
=============
CRUD + run interface for the Scripts configuration page.
"""
import io
import sys
import time
import traceback
from datetime import datetime, timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ScriptForm
from .models import Script, ScriptExecution


# ── List ──────────────────────────────────────────────────────────────────────

@login_required
def script_list(request):
    scripts = Script.objects.prefetch_related('executions').all()

    # Summary counts
    total    = scripts.count()
    active   = scripts.filter(status='ACTIVE').count()
    inactive = scripts.filter(status='INACTIVE').count()
    draft    = scripts.filter(status='DRAFT').count()

    # Filter by type / status from querystring
    type_filter   = request.GET.get('type', '')
    status_filter = request.GET.get('status', '')
    search        = request.GET.get('q', '').strip()

    qs = scripts
    if type_filter:
        qs = qs.filter(script_type=type_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(name__icontains=search)

    return render(request, 'scripts/script_list.html', {
        'scripts':        qs,
        'total':          total,
        'active':         active,
        'inactive':       inactive,
        'draft':          draft,
        'type_filter':    type_filter,
        'status_filter':  status_filter,
        'search':         search,
        'script_types':   Script.ScriptType.choices,
        'status_choices': Script.Status.choices,
    })


# ── Create ─────────────────────────────────────────────────────────────────────

@login_required
def script_create(request):
    form = ScriptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        script = form.save(commit=False)
        script.created_by = request.user
        script.save()
        messages.success(request, f'Script "{script.name}" created successfully.')
        return redirect('scripts:script_list')
    return render(request, 'scripts/script_form.html', {
        'form': form,
        'title': 'New Script',
        'btn_label': 'Create Script',
    })


# ── Edit ───────────────────────────────────────────────────────────────────────

@login_required
def script_edit(request, pk):
    script = get_object_or_404(Script, pk=pk)
    form = ScriptForm(request.POST or None, instance=script)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Script "{script.name}" updated.')
        return redirect('scripts:script_list')
    return render(request, 'scripts/script_form.html', {
        'form': form,
        'script': script,
        'title': f'Edit — {script.name}',
        'btn_label': 'Save Changes',
    })


# ── Detail ─────────────────────────────────────────────────────────────────────

@login_required
def script_detail(request, pk):
    script = get_object_or_404(Script, pk=pk)
    executions = script.executions.all()[:20]
    return render(request, 'scripts/script_detail.html', {
        'script': script,
        'executions': executions,
    })


# ── Delete ─────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def script_delete(request, pk):
    script = get_object_or_404(Script, pk=pk)
    name = script.name
    script.delete()
    messages.success(request, f'Script "{name}" deleted.')
    return redirect('scripts:script_list')


# ── Run ────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def script_run(request, pk):
    """
    Execute the script in-process (Python only for now).
    Returns JSON so the UI can show live output without a page reload.
    """
    script = get_object_or_404(Script, pk=pk)

    execution = ScriptExecution.objects.create(
        script=script,
        run_by=request.user,
        run_status=ScriptExecution.RunStatus.RUNNING,
    )

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    t_start = time.monotonic()
    run_status = ScriptExecution.RunStatus.SUCCESS
    error_text = ''

    try:
        if script.script_type == Script.ScriptType.PYTHON:
            exec_globals = {
                '__builtins__': __builtins__,
                'print': lambda *a, **kw: print(*a, file=stdout_capture, **kw),
            }
            exec(compile(script.content, script.name, 'exec'), exec_globals)  # noqa: S102
        else:
            stdout_capture.write(f'[INFO] Dry-run only — {script.script_type} execution not enabled in this environment.\n')

    except Exception:
        run_status = ScriptExecution.RunStatus.FAILED
        error_text = traceback.format_exc()

    duration_ms = int((time.monotonic() - t_start) * 1000)

    execution.run_status  = run_status
    execution.output      = stdout_capture.getvalue()
    execution.error       = error_text
    execution.finished_at = datetime.now(tz=timezone.utc)
    execution.duration_ms = duration_ms
    execution.save()

    return JsonResponse({
        'status':      run_status,
        'output':      execution.output,
        'error':       error_text,
        'duration_ms': duration_ms,
        'execution_id': execution.pk,
    })


# ── IDE ────────────────────────────────────────────────────────────────────────

@login_required
def script_ide(request, pk=None):
    """Unified IDE interface for scripts."""
    from collections import defaultdict
    
    scripts = Script.objects.all().order_by('name')
    
    # Group by category display name
    grouped_scripts = defaultdict(list)
    for script in scripts:
        grouped_scripts[script.get_category_display()].append({
            'id': script.pk,
            'name': script.name,
        })
    
    # Sort groups
    grouped_scripts = dict(sorted(grouped_scripts.items()))

    selected_script = None
    form = None

    if pk:
        selected_script = get_object_or_404(Script, pk=pk)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete' and selected_script:
            selected_script.delete()
            messages.success(request, 'Script deleted.')
            return redirect('scripts:script_ide')
        
        form = ScriptForm(request.POST, instance=selected_script)
        if form.is_valid():
            saved_script = form.save(commit=False)
            if not saved_script.pk:
                saved_script.created_by = request.user
            saved_script.save()
            messages.success(request, 'Script saved successfully.')
            return redirect('scripts:script_ide_edit', pk=saved_script.pk)
    else:
        form = ScriptForm(instance=selected_script)

    return render(request, 'scripts/script_ide.html', {
        'grouped_scripts': grouped_scripts,
        'selected_script': selected_script,
        'form': form,
    })
