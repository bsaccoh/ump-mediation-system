import csv
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO

from django.db.models import Sum

from regulatory.models import AuditCase, OperatorDeclaration, ReconciliationRun, RevenueSnapshot, RiskAlert, TrafficSummary


class ReportGenerator:
    REPORTS = {
        'traffic': ('Regulatory Traffic Report', TrafficSummary, ['operator_code','service_type','traffic_type','record_count']),
        'international_traffic': ('International Traffic Report', TrafficSummary, ['operator_code','service_type','record_count']),
        'roaming_traffic': ('Roaming Traffic Report', TrafficSummary, ['operator_code','service_type','record_count']),
        'interconnect_traffic': ('Interconnect Traffic Report', TrafficSummary, ['operator_code','service_type','record_count']),
        'expected_revenue': ('Expected Revenue Report', RevenueSnapshot, ['operator_code','period_date','expected_revenue','expected_tax']),
        'declared_revenue': ('Declared Revenue Report', OperatorDeclaration, ['operator_code','period_start','total_declared_revenue','total_declared_tax','status']),
        'reconciliation': ('Reconciliation Report', ReconciliationRun, ['reference','operator_code','level','expected_revenue','declared_revenue','revenue_variance','status']),
        'risk': ('Risk Report', RiskAlert, ['reference','operator_code','alert_type','severity','metric_value','threshold_value','status']),
        'audit_cases': ('Audit Cases Report', AuditCase, ['case_number','operator_code','case_type','risk_level','status','amount_at_risk']),
    }
    def __init__(self, report_type, filters=None): self.report_type,self.filters=report_type,filters or {}
    def data(self):
        title,model,fields=self.REPORTS.get(self.report_type,self.REPORTS['traffic']); qs=model.objects.all()
        operator=self.filters.get('operator'); start=self.filters.get('start_date'); end=self.filters.get('end_date')
        if operator and hasattr(model,'operator_code'): qs=qs.filter(operator_code=operator)
        date_field=next((field for field in ('period_date','period_start','triggered_at','created_at') if any(f.name==field for f in model._meta.fields)),None)
        if start and date_field: qs=qs.filter(**{f'{date_field}__date__gte' if 'at' in date_field else f'{date_field}__gte':start})
        if end and date_field: qs=qs.filter(**{f'{date_field}__date__lte' if 'at' in date_field else f'{date_field}__lte':end})
        if self.report_type=='international_traffic': qs=qs.filter(traffic_type='INTERNATIONAL')
        if self.report_type=='roaming_traffic': qs=qs.filter(traffic_type='ROAMING')
        if self.report_type=='interconnect_traffic': qs=qs.filter(traffic_type='INTERCONNECT')
        return title,fields,list(qs.values(*fields)[:10000])
    def render(self, output_format):
        title,fields,rows=self.data()
        if output_format=='csv':
            stream=StringIO(); writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(rows); return stream.getvalue().encode(), 'text/csv', 'csv'
        if output_format=='excel':
            from openpyxl import Workbook
            wb=Workbook(); ws=wb.active; ws.title='Report'; ws.append([title]); ws.append(fields)
            for row in rows: ws.append([row.get(field,'') for field in fields])
            ws.freeze_panes='A3'; out=BytesIO(); wb.save(out); return out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','xlsx'
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
        out=BytesIO(); doc=SimpleDocTemplate(out,pagesize=landscape(A4)); table=[fields]+[[str(row.get(field,'')) for field in fields] for row in rows] or [fields]
        doc.build([Paragraph(title, getSampleStyleSheet()['Title']),Spacer(1,12),Table(table)]); return out.getvalue(),'application/pdf','pdf'
