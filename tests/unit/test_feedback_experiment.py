"""Small deterministic checks; the real encoder experiment is explicitly invoked."""
import math
from newspaper_agent.research.feedback_experiment import dataset, ranking_metrics, simulate, aggregate, controls
from newspaper_agent.research.feedback_experiment_report import chart
from newspaper_agent.semantic_profile import unit


def fixture_data():
    _,meta=dataset()
    vectors={'theory':unit([.9,.3,.0,.0]), 'materials':unit([.9,-.3,0.,0.]),
             'reliability':unit([0.,0.,.9,.3]),'consumer':unit([0.,0.,.9,-.3]),'cooking':[0.,-1.,0.,0.]}
    docs={eid:{'vector':vectors[m['topic']],'domains':['academic'],'tags':[]} for eid,m in meta.items()}
    slots=[]
    for name,anchor in [('Condensed matter',[1.,0.,0.,0.]),('AI systems',[0.,0.,1.,0.])]:
        slots.append({'id':name,'category':name,'topics':[name],'domains':['academic'],
            'anchor':anchor,'vector':list(anchor),'positive_mass':0.,'negative_mass':0.,
            'observations':0,'angle':0.,'affinity_bias':0.})
    return slots,docs,meta


def test_feedback_and_evaluation_facets_do_not_overlap():
    rows,meta=dataset()
    assert len(rows)==len(meta)==120 and all(r.is_fixture for r in rows)
    train={(m['topic'],m['facet']) for m in meta.values() if m['split']=='train'}
    test={(m['topic'],m['facet']) for m in meta.values() if m['split']=='test'}
    assert not train & test
    assert sum(m['split']=='test' for m in meta.values())==40


def test_metrics_have_known_perfect_and_worst_values():
    labels={'a':True,'b':False};topics={'a':'one','b':'two'}
    assert ranking_metrics(['a','b'],labels,topics,1)['ndcg']==1
    assert ranking_metrics(['b','a'],labels,topics,1)['precision']==0


def test_simulation_is_reproducible_and_within_bounds():
    slots,docs,meta=fixture_data()
    first,snapshots=simulate(slots,docs,meta,seeds=2)
    second,_=simulate(slots,docs,meta,seeds=2)
    assert first==second and len(first)==2*4*7
    assert all(0<=r['precision']<=1 and 0<=r['ndcg']<=1 and r['angle']<=25.0001 for r in first)
    assert snapshots['clean:0'][0]['vector']==slots[0]['anchor']
    groups=aggregate(first)
    assert len(groups)==28 and all(r['precision']['ci95']>=0 for r in groups)
    data=controls(slots,docs,meta)
    assert data['undo_max_delta']==data['replay_max_delta']==data['other_category_max_delta']==0
    assert math.isclose(data['positive_mass_after_180_days'],data['positive_mass_now']/4)


def test_chart_is_well_formed_and_uses_requested_seed_count():
    import xml.etree.ElementTree as ET
    slots,docs,meta=fixture_data()
    rows,_=simulate(slots,docs,meta,seeds=2)
    svg=chart({'aggregate':aggregate(rows),'seeds':2})
    assert '2 个固定种子' in svg
    root=ET.fromstring(svg)
    assert len(root.findall('.//{http://www.w3.org/2000/svg}polyline'))==26
