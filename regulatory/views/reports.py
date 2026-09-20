from django.http import HttpResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from core.decorators import regulator_required
from reference.models import Operator
from regulatory.services.report_generator import ReportGenerator

@login_required
@regulator_required
def report_hub(request): return render(request,'regulatory/reports/hub.html',{'reports':ReportGenerator.REPORTS,'operators':Operator.objects.filter(enabled=True)})
@login_required
@regulator_required
def report_generate(request): return report_download(request,request.POST.get('report_type','traffic'))
@login_required
@regulator_required
def report_download(request,report_type):
    content,mime,extension=ReportGenerator(report_type,request.POST or request.GET).render((request.POST or request.GET).get('format','pdf'))
    response=HttpResponse(content,content_type=mime); response['Content-Disposition']=f'attachment; filename="{report_type}.{extension}"'; return response
