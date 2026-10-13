// Defense in depth for local templates: parse inertly, sanitize, then mount nodes.
// Dynamic record strings must still use h() in templates.
const tags=new Set('div span main aside nav a button form input select option optgroup label textarea h1 h2 h3 h4 p strong b small em i code pre br hr section header footer article details summary ul ol li table caption colgroup col thead tbody tfoot tr th td svg path circle rect ellipse line polyline polygon g'.split(' '));
const attrs=new Set('id class role title type name value placeholder readonly disabled required multiple checked selected open hidden min max step maxlength accept inputmode tabindex download href action method for colspan rowspan scope autocomplete viewbox d fill stroke stroke-width stroke-linecap stroke-linejoin cx cy r rx ry x y x1 y1 x2 y2 width height points xmlns'.split(' '));
function safeURL(value) {
  try {
    const url=new URL(value,location.href);
    return ['http:','https:'].includes(url.protocol)&&url.origin===location.origin;
  } catch {return false;}
}
function parseHTML(markup) {
  const parsed=new DOMParser().parseFromString(markup,'text/html');
  for(const node of Array.from(parsed.body.querySelectorAll('*'))){
    if(!tags.has(node.localName.toLowerCase())){node.remove();continue;}
    for(const attribute of Array.from(node.attributes)){
      const key=attribute.name.toLowerCase();
      if(key==='style'){
        // Used only for local bar widths and fixed spacing; reject CSS URL/script sinks.
        if(attribute.value.includes('\\')||attribute.value.includes('/*')||/url\s*\(|expression\s*\(|@import|javascript:|behavior\s*:|-moz-binding/i.test(attribute.value))node.removeAttribute(attribute.name);
      } else if(key==='href'||key==='action'){
        if(!safeURL(attribute.value))node.removeAttribute(attribute.name);
      } else if(key.startsWith('on')||(!attrs.has(key)&&!key.startsWith('aria-')&&!key.startsWith('data-'))){
        node.removeAttribute(attribute.name);
      }
    }
  }
  return parsed.body;
}
export function mountHTML(target,markup) {
  const body=parseHTML(markup);
  target.replaceChildren(...Array.from(body.childNodes,node=>document.importNode(node,true)));
}

// Keep unchanged controls, rows and scroll containers alive between updates.
// Templates still pass through the same sanitizer as the initial mount.
function key(node) {
  if(node.nodeType!==1)return null;
  if(node.id)return `id:${node.id}`;
  for(const attribute of ['data-student-row','data-person-key','data-row-key']){
    if(node.hasAttribute(attribute))return `${attribute}:${node.getAttribute(attribute)}`;
  }
  if(node.hasAttribute('data-action'))return `action:${node.dataset.action}:${node.dataset.id||node.dataset.student||node.dataset.key||node.dataset.sort||node.dataset.scope||''}`;
  return null;
}
function compatible(a,b) {
  return a.nodeType===b.nodeType&&(a.nodeType!==1||a.localName===b.localName&&a.namespaceURI===b.namespaceURI);
}
function patchNode(current,next) {
  if(current.nodeType!==1){if(current.nodeValue!==next.nodeValue)current.nodeValue=next.nodeValue;return;}
  const value=next.value,checked=next.checked;
  // An open disclosure belongs to the user; an unrelated data update must not close it.
  for(const attr of Array.from(current.attributes)){
    if(attr.name==='open'&&current.localName==='details')continue;
    if(!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
  }
  for(const attr of Array.from(next.attributes)){
    if(current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
  }
  patchChildren(current,next);
  if(current.localName==='input'){
    if(current.type!=='file'&&current.value!==value)current.value=value;
    if(['checkbox','radio'].includes(current.type))current.checked=checked;
  } else if(current.localName==='select'){
    current.value=value;
  }
}
function patchChildren(target,source) {
  const old=Array.from(target.childNodes),used=new Set();
  const keyed=new Map(old.map(node=>[key(node),node]).filter(([id])=>id));
  let cursor=target.firstChild;
  for(const next of Array.from(source.childNodes)){
    const id=key(next);
    let current=id?keyed.get(id):null;
    if(!id)current=old.find(node=>!used.has(node)&&!key(node)&&compatible(node,next));
    if(current&&(!compatible(current,next)||used.has(current)))current=null;
    if(current){
      used.add(current);patchNode(current,next);
      if(current!==cursor)target.insertBefore(current,cursor);
    } else {
      current=next;target.insertBefore(current,cursor);
    }
    cursor=current.nextSibling;
  }
  for(const node of old)if(!used.has(node)&&node.parentNode===target)node.remove();
}
export function updateHTML(target,markup,prepare) {
  const body=parseHTML(markup);
  prepare?.(body);
  patchChildren(target,body);
}
