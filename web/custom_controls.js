// Native selects remain form values only; the visible UI is entirely custom.
let openControl=null, sequence=0;

export function closeControl(returnFocus=false) {
  if(!openControl)return;
  const {button,popup}=openControl;
  button.setAttribute('aria-expanded','false');
  popup.remove();openControl=null;
  if(returnFocus&&button.isConnected)button.focus({preventScroll:true});
}
function positionControl() {
  if(!openControl)return;
  const {button,popup}=openControl;
  if(!button.isConnected){closeControl();return;}
  const rect=button.getBoundingClientRect();
  if(rect.bottom<0||rect.top>innerHeight){closeControl();return;}
  const width=Math.min(Math.max(rect.width,180),innerWidth-24);
  popup.style.width=`${width}px`;
  popup.style.left=`${Math.max(12,Math.min(rect.left,innerWidth-width-12))}px`;
  const below=innerHeight-rect.bottom-16,above=rect.top-16,flip=below<180&&above>below;
  popup.style.maxHeight=`${Math.max(80,Math.min(300,flip?above:below))}px`;
  popup.style.top=`${flip?Math.max(12,rect.top-popup.offsetHeight-6):rect.bottom+6}px`;
}
function showControl(select,button) {
  if(openControl?.button===button){closeControl(true);return;}
  closeControl();
  const popup=document.createElement('div');
  popup.id=button.getAttribute('aria-controls');popup.className='control-popover';popup.setAttribute('role','listbox');
  popup.setAttribute('aria-label',select.getAttribute('aria-label')||button.textContent);
  const options=Array.from(select.options);
  for(const option of options){
    const item=document.createElement('button');item.type='button';item.className='control-option';
    item.setAttribute('role','option');item.setAttribute('aria-selected',String(option.selected));
    item.disabled=option.disabled;item.dataset.value=option.value;
    const text=document.createElement('span');text.textContent=option.textContent;
    const tick=document.createElement('span');tick.className='option-tick';tick.setAttribute('aria-hidden','true');tick.textContent=option.selected?'✓':'';
    item.append(text,tick);popup.append(item);
    item.addEventListener('click',()=>{
      select.value=option.value;
      closeControl(true);
      button.querySelector('.control-label').textContent=option.textContent;
      select.dispatchEvent(new Event('change',{bubbles:true}));
    });
  }
  (select.closest('dialog[open]')||document.body).append(popup);
  openControl={select,button,popup};button.setAttribute('aria-expanded','true');
  positionControl();
  (popup.querySelector('[aria-selected="true"]')||popup.querySelector('button:not(:disabled)'))?.focus({preventScroll:true});
}
export function captureControl(root) {
  if(!openControl||!root?.contains(openControl.select))return null;
  const snapshot={id:openControl.select.id,focusedValue:document.activeElement?.dataset?.value};
  closeControl();return snapshot;
}
export function restoreControl(snapshot) {
  if(!snapshot||openControl)return;
  const select=document.getElementById(snapshot.id),button=document.getElementById(`${snapshot.id}-trigger`);
  if(!select||!button||button.disabled)return;
  showControl(select,button);
  if(snapshot.focusedValue!==undefined){
    const option=Array.from(openControl.popup.querySelectorAll('button')).find(item=>item.dataset.value===snapshot.focusedValue);
    option?.focus({preventScroll:true});
  }
}
export function enhanceControls(root=document) {
  for(const select of root.querySelectorAll('select:not([data-customized])')){
    select.dataset.customized='true';select.hidden=true;select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    if(!select.id)select.id=`custom-select-${++sequence}`;
    const wrapper=document.createElement('div');wrapper.className='custom-select';
    select.before(wrapper);wrapper.append(select);
    const button=document.createElement('button');button.type='button';button.id=`${select.id}-trigger`;
    button.className='control-trigger';button.disabled=select.disabled;button.setAttribute('role','combobox');
    button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',`${select.id}-options`);
    const label=document.querySelector(`label[for="${select.id}"]`);
    const accessible=select.getAttribute('aria-label')||label?.textContent?.trim()||'选择选项';
    button.setAttribute('aria-label',accessible);
    if(label)label.htmlFor=button.id;
    const text=document.createElement('span');text.className='control-label';text.textContent=select.selectedOptions[0]?.textContent||'请选择';
    const arrow=document.createElement('span');arrow.className='control-arrow';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','m6 9 6 6 6-6');
    svg.append(path);arrow.append(svg);
    button.append(text,arrow);wrapper.append(button);
    button.addEventListener('click',()=>showControl(select,button));
    button.addEventListener('keydown',event=>{
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();showControl(select,button);}
    });
    select.addEventListener('change',()=>{text.textContent=select.selectedOptions[0]?.textContent||'请选择';});
  }
}
document.addEventListener('pointerdown',event=>{
  for(const menu of document.querySelectorAll('.export-menu[open]'))if(!menu.contains(event.target))menu.open=false;
  if(openControl&&!openControl.popup.contains(event.target)&&!openControl.button.contains(event.target))closeControl();
});
document.addEventListener('keydown',event=>{
  if(!openControl){
    if(event.key==='Escape'){
      const menu=document.querySelector('.export-menu[open]');
      if(menu){event.preventDefault();menu.open=false;menu.querySelector('summary').focus({preventScroll:true});}
    }
    return;
  }
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeControl(true);return;}
  if(event.key==='Tab'){closeControl();return;}
  const items=Array.from(openControl.popup.querySelectorAll('button:not(:disabled)'));
  const current=items.indexOf(document.activeElement);
  let next=current;
  if(event.key==='ArrowDown')next=(current+1)%items.length;
  else if(event.key==='ArrowUp')next=(current-1+items.length)%items.length;
  else if(event.key==='Home')next=0;
  else if(event.key==='End')next=items.length-1;
  else return;
  event.preventDefault();items[next]?.focus({preventScroll:true});items[next]?.scrollIntoView({block:'nearest'});
});
window.addEventListener('resize',positionControl);
window.addEventListener('scroll',event=>{if(openControl&&!openControl.popup.contains(event.target))positionControl();},true);
