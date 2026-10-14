"""Review-only browser regressions against a synthetic temporary test server."""
from tests.test_app import HEADERS, row
from tests.test_export_format_ui import seed
from tests.test_roster_removal_ui import upload_payload
from tests.test_ui_shell import browser as review_browser
from tests.test_ui_shell import playwright
from tests.test_ui_shell import ui_url as review_url

browser=review_browser
ui_url=review_url
WRITE={'X-Requested-With':'StudentScore'}


def select_class(page,url,sid):
    student=page.request.get(url+f'/api/students/{sid}').json()
    page.goto(url+'/#scores')
    page.locator(f'[data-action="select-college"][data-id="{student["college_id"]}"]').click()
    page.locator(f'[data-action="select-class"][data-id="{student["class_id"]}"]').click()
    playwright.expect(page.locator('.score-table-scroll')).to_have_attribute('aria-busy','false')


def test_action_discriminators_keep_nodes_and_keyboard_focus(browser,ui_url):
    page=browser.new_page()
    try:
        page.goto(ui_url)
        result=page.evaluate('''async () => {
          const {mountHTML,updateHTML}=await import('/static/safe_dom.js');
          const root=document.createElement('div');document.body.append(root);
          const markup='<button data-action="export" data-format="xlsx">Excel</button><button data-action="export" data-format="csv">CSV</button>';
          mountHTML(root,markup);
          const excel=root.children[0],csv=root.children[1];csv.focus();
          updateHTML(root,markup.replace('Excel','Excel更新'));
          const stable=root.children[0]===excel&&root.children[1]===csv&&document.activeElement===csv&&csv.dataset.format==='csv';
          root.remove();return stable;
        }''')
        assert result
    finally:
        page.close()


def test_base_snapshots_retry_mismatches_and_never_publish_stale_callbacks(browser,ui_url):
    page=browser.new_page()
    try:
        page.goto(ui_url)
        result=page.evaluate('''async () => {
          const {readBaseSnapshot}=await import('/static/read_snapshots.js');
          let stats=0,hierarchy=0;
          const api=async path=>path==='/api/stats'?{revision:++stats}:{revision:++hierarchy===1?2:2,colleges:[]};
          const data=await readBaseSnapshot(api,{includeHistory:false});
          const discarded=await readBaseSnapshot(api,{includeHistory:false,isCurrent:()=>false});
          let refused=false;
          try{await readBaseSnapshot(async path=>({revision:path==='/api/stats'?1:2}),{includeHistory:false});}catch{refused=true;}
          return {revision:data.stats.revision,discarded,refused};
        }''')
        assert result=={'revision':2,'discarded':None,'refused':True}
    finally:
        page.close()


def test_follow_up_failure_preserves_results_but_disables_export(browser,ui_url):
    page=browser.new_page()
    try:
        seed(page,ui_url,[row('REVIEW-FOLLOW',cls='审查跟进班',status='已完成',rate=None)])
        select_class(page,ui_url,'REVIEW-FOLLOW')
        page.locator('[data-action="follow-open"]').click()
        dialog=page.locator('#follow-up-dialog')
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(1)
        page.route('**/api/follow-up?*',lambda route:route.fulfill(status=503,json={'detail':'合成刷新失败'}))
        dialog.locator('.follow-toolbar [data-action="follow-refresh"]').click()
        playwright.expect(dialog.locator('[role="alert"]')).to_contain_text('合成刷新失败')
        playwright.expect(dialog.locator('[role="status"]')).to_contain_text('上次读取结果')
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(1)
        playwright.expect(dialog.locator('[data-action="follow-export"][data-format="xlsx"]')).to_be_disabled()
    finally:
        page.close()


def test_closed_follow_up_session_cannot_apply_old_export_error(browser,ui_url):
    page=browser.new_page()
    try:
        seed(page,ui_url,[row('REVIEW-LATE',cls='审查延迟班',status='已完成',rate=None)])
        select_class(page,ui_url,'REVIEW-LATE')
        page.locator('[data-action="follow-open"]').click()
        dialog=page.locator('#follow-up-dialog')
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(1)
        page.evaluate('''() => {
          const original=window.fetch;
          window.fetch=(url,options)=>String(url).includes('/api/follow-up/export.')?
              new Promise((resolve,reject)=>window.rejectOldExport=reject):original(url,options);
        }''')
        dialog.locator('[data-action="follow-export"][data-format="csv"]').click()
        dialog.locator('.dialog-header [data-action="follow-close"]').click()
        page.locator('[data-action="follow-open"]').click()
        playwright.expect(dialog.locator('#follow-content [data-row-key]')).to_have_count(1)
        page.evaluate("rejectOldExport(new Error('旧会话导出失败'))")
        playwright.expect(dialog.locator('[data-action="follow-export"][data-format="xlsx"]')).to_be_enabled()
        assert dialog.locator('[role="alert"]').count()==0
        assert '旧会话' not in page.locator('#toast').inner_text()
    finally:
        page.close()


def test_official_export_refuses_changed_revision_without_downloading(browser,ui_url):
    page=browser.new_page(accept_downloads=True)
    downloads=[]
    page.on('download',lambda item:downloads.append(item))
    try:
        seed(page,ui_url,[row('REVIEW-REV',cls='审查版本班',status='已完成',rate=None)])
        select_class(page,ui_url,'REVIEW-REV')
        student=page.request.get(ui_url+'/api/students/REVIEW-REV').json()
        assert page.request.patch(ui_url+'/api/students/REVIEW-REV',headers=WRITE,data={
            'version':student['version'],'status':'已完成','score_rate':95}).ok
        page.locator('.export-menu summary').click()
        page.locator('[data-action="score-export"]').click()
        playwright.expect(page.locator('#toast')).to_contain_text('数据已有变更')
        assert not downloads
    finally:
        page.close()


def test_saved_roster_refresh_failure_is_not_reported_as_unsaved(browser,ui_url):
    page=browser.new_page()
    try:
        page.goto(ui_url+'/#import')
        page.locator('#file-input').set_input_files(upload_payload([HEADERS,row('REVIEW-SAVED',cls='审查已保存班')]))
        page.locator('[data-action="preview"]').click()
        page.locator('#preview').wait_for()
        page.route('**/api/hierarchy',lambda route:route.fulfill(status=503,json={'detail':'合成目录刷新失败'}))
        page.locator('[data-action="commit"]').click()
        playwright.expect(page.locator('.success-panel h2')).to_have_text('学生名单已写入数据库')
        playwright.expect(page.locator('.success-panel [role="alert"]')).to_contain_text('名单已保存')
        playwright.expect(page.locator('.success-panel [role="alert"]')).to_contain_text('勿重复导入')
        assert page.request.get(ui_url+'/api/students/REVIEW-SAVED').ok
    finally:
        page.close()
