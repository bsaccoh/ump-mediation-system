from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from drive_test.access import dt_view_required
from drive_test.models import Campaign
from drive_test.services.ai_analyst import analyze
from drive_test.services.ai_llm import llm_enabled, narrate
from drive_test.services.audit import log_action


@login_required
@dt_view_required
def ai_analyst(request):
    campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(sample_count=Count('samples')).filter(sample_count__gt=0)
        .order_by('-created_at')
    )
    campaign = None
    analysis = None
    narrative = None
    question = request.GET.get('question', '').strip()
    cid = request.GET.get('campaign')

    if cid:
        campaign = get_object_or_404(Campaign, pk=cid)
        analysis = analyze(campaign, question)
        if analysis.get('has_data'):
            narrative = narrate(analysis)  # None unless the LLM is enabled
            log_action(request.user, 'PROCESS', 'drive_test.Campaign', campaign.pk,
                       'Ran AI analyst', request=request)

    return render(request, 'drive_test/ai_analyst.html', {
        'campaigns': campaigns,
        'campaign': campaign,
        'analysis': analysis,
        'narrative': narrative,
        'question': question,
        'llm_enabled': llm_enabled(),
    })
