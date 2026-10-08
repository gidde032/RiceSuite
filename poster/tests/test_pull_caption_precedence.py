"""Incident regression: Pull must sync the displayed and requested style.

Real page functions run with a fake DOM/transport; no live data or APIs.
"""
import json
import re
import shutil
import subprocess

import pytest
from tests.test_frontend_robustness import _function_body, _script


def run_page(driver):
    node = shutil.which('node')
    if not node:
        pytest.skip('node needed for frontend behavior checks')
    script = _script()
    functions = []
    for name in ('emptySlot', 'knownStyle', 'styleOptions', 'syncCaptionStyle',
                 'applyPulledSlot', 'generateAll', 'regenerateCaption',
                 'captureDrafts', 'applyRestoredDraft', 'setCaptionDefault', 'resetRun'):
        head = re.search(rf'(?:async )?function {name}\([^)]*\)\s*', script)
        if head:
            functions.append(head.group() + _function_body(name))
    prelude = """
const state = {
 captionStyles:['generic','sports','music-fanpage'].map(name=>({name,display_name:name})),
 defaultCaptionStyle:'generic',accountState:{caption_defaults:{A:'sports'}},
 slots:{},accounts:[{slot:'A'}],
};
const esc=String,escAttr=String;
const picker={value:'sports',set innerHTML(html){this.value=html.match(/value="([^"]+)" selected/)[1];}};
const nodes={};
const el=id=>nodes[id]||={value:'',textContent:'',disabled:false,style:{}};
const slotEl=(kind,slot)=>el(kind+'_'+slot);
const slotElOpt=(kind,slot)=>kind==='captionStyle'?picker:null;
const clearSlotPreview=()=>{},updateCharCount=()=>{},setCaptionError=()=>{};
const renderSlotPreview=()=>{},autoGrow=()=>{},updateButtons=()=>{};
const renderSlots=()=>{},renderAccounts=()=>{},renderSummary=()=>{},persistAccountState=async()=>{};
const handleFetchError=async()=>{};
let accountChangeInFlight=false,draftWork=0;
const CAPTION_TIMEOUT_MS=65000,requests=[];
const fetchWithTimeout=async(url,options)=>{
 requests.push(Object.fromEntries(options.body.entries()));
 return {json:async()=>({caption:'mock caption'})};
};
const entry={slot:'A',filename:'fixture.mp4',media_type:'video',topic:'fixture',style:'music-fanpage'};
"""
    result = subprocess.run([node, '--input-type=module'],
                            input=prelude+'\n'+'\n'.join(functions)+'\n'+driver,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('changed,current,account,ingest,expected', [
    (True, 'generic', 'sports', 'music-fanpage', 'generic'),
    (False, 'generic', 'sports', 'music-fanpage', 'sports'),
    (False, 'sports', None, 'music-fanpage', 'music-fanpage'),
    (True, 'removed', 'sports', 'music-fanpage', 'sports'),
    (False, 'generic', 'removed', 'music-fanpage', 'music-fanpage'),
    (False, 'sports', None, 'removed', 'generic'),
])
def test_pull_style_precedence_and_display(changed, current, account, ingest, expected):
    result = run_page(f"""
state.slots.A={{...emptySlot(),style:{json.dumps(current)},reviewStyleChanged:{json.dumps(changed)}}};
state.accountState.caption_defaults.A={json.dumps(account)};
entry.style={json.dumps(ingest)};
picker.value={json.dumps(current)};
applyPulledSlot(entry);
await generateAll();
await regenerateCaption('A');
console.log(JSON.stringify({{actual:state.slots.A.style,displayed:picker.value,sent:requests.map(r=>r.style)}}));
""")
    assert result == {'actual': expected, 'displayed': expected, 'sent': [expected, expected]}


def test_normalization_updates_display_before_regeneration():
    result = run_page("""
state.slots.A={...emptySlot(),filename:'fixture.mp4',style:'removed',reviewStyleChanged:true};
picker.value='generic';
await regenerateCaption('A');
console.log(JSON.stringify({actual:state.slots.A.style,displayed:picker.value,sent:requests[0].style,changed:state.slots.A.reviewStyleChanged}));
""")
    assert result == {'actual': 'sports', 'displayed': 'sports', 'sent': 'sports', 'changed': False}


def test_review_picker_marks_an_explicit_choice():
    handler = re.search(
        r'<select id="captionStyle_\$\{slot\}"[^>]*onchange="([^"]+)"', _script()
    ).group(1).replace('${slot}', 'A')
    result = run_page(f"""
state.slots.A={{...emptySlot(),style:'sports'}};
const change=function(){{{handler}}};
change.call({{value:'generic'}});
applyPulledSlot(entry);
console.log(JSON.stringify({{style:state.slots.A.style,changed:state.slots.A.reviewStyleChanged}}));
""")
    assert result == {'style': 'generic', 'changed': True}


def test_restore_preserves_override_only_for_the_same_account():
    result = run_page("""
state.slots.A={...emptySlot(),filename:'fixture.mp4',style:'generic',reviewStyleChanged:true};
const saved=captureDrafts()[0];
applyRestoredDraft('A',saved);
const same=state.slots.A.reviewStyleChanged;
applyRestoredDraft('A',{...saved,account:'B'});
console.log(JSON.stringify({same,moved:state.slots.A.reviewStyleChanged}));
""")
    assert result == {'same': True, 'moved': False}


def test_account_default_does_not_override_explicit_review_choice():
    result = run_page("""
state.slots.A={...emptySlot(),style:'generic',reviewStyleChanged:true};
await setCaptionDefault('A','sports');
console.log(JSON.stringify({review:state.slots.A.style,saved:state.accountState.caption_defaults.A}));
""")
    assert result == {'review': 'generic', 'saved': 'sports'}


def test_prior_pull_is_not_promoted_to_an_explicit_review_choice():
    result = run_page("""
state.slots.A={...emptySlot(),style:'generic'};
state.accountState.caption_defaults={};
applyPulledSlot(entry);
const first=state.slots.A.style;
state.accountState.caption_defaults.A='sports';
applyPulledSlot(entry);
console.log(JSON.stringify({first,second:state.slots.A.style,displayed:picker.value,changed:state.slots.A.reviewStyleChanged}));
""")
    assert result == {'first': 'music-fanpage', 'second': 'sports',
                      'displayed': 'sports', 'changed': False}


def test_removed_restored_override_does_not_promote_the_fallback_style():
    result = run_page("""
state.slots.A=emptySlot();
applyRestoredDraft('A',{account:'A',style:'removed',reviewStyleChanged:true});
state.accountState.caption_defaults.A='generic';
applyPulledSlot(entry);
console.log(JSON.stringify({style:state.slots.A.style,changed:state.slots.A.reviewStyleChanged}));
""")
    assert result == {'style': 'generic', 'changed': False}


def test_new_run_keeps_the_explicit_review_choice():
    result = run_page("""
state.slots.A={...emptySlot(),filename:'fixture.mp4',style:'generic',reviewStyleChanged:true};
resetRun();
applyPulledSlot(entry);
console.log(JSON.stringify({style:state.slots.A.style,changed:state.slots.A.reviewStyleChanged}));
""")
    assert result == {'style': 'generic', 'changed': True}
