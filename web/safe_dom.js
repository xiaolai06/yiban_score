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
export function mountHTML(target,markup) {
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
  target.replaceChildren(...Array.from(parsed.body.childNodes,node=>document.importNode(node,true)));
}
