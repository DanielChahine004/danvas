// Compact Markdown -> HTML for the *preview* only (instant, client-side). The
// published board is rendered by Python's richer converter. (Regexes here avoid
// char classes with brackets/backticks so the startup lint's brace-scan stays
// happy; lazy groups do the same job.)
function annEsc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function annInline(s){
  return annEsc(s)
    .replace(/`(.+?)`/g,"<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>")
    .replace(/\*(.+?)\*/g,"<em>$1</em>")
    .replace(/\[(.+?)\]\((.+?)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function annFence(l){ return l.trim().slice(0,3) === "" + "``" + "`"; }
function annIsBlock(l){
  return /^(#{1,3})\s+/.test(l) || /^\s*([-*]|\d+\.)\s+/.test(l)
      || /^\s*>/.test(l) || annFence(l) || /^\s*(---|\*\*\*)\s*$/.test(l);
}
function annMd(src){
  const lines=(src||"").split("\n"); const out=[]; let i=0;
  while(i<lines.length){
    const ln=lines[i];
    if(annFence(ln)){ i++; const code=[];
      while(i<lines.length && !annFence(lines[i])){ code.push(annEsc(lines[i])); i++; }
      i++; out.push("<pre><code>"+code.join("\n")+"</code></pre>"); continue; }
    const h=ln.match(/^(#{1,3})\s+(.*)/);
    if(h){ const l=h[1].length; out.push("<h"+l+">"+annInline(h[2])+"</h"+l+">"); i++; continue; }
    if(/^\s*(---|\*\*\*)\s*$/.test(ln)){ out.push("<hr>"); i++; continue; }
    if(/^\s*>\s?/.test(ln)){ const q=[];
      while(i<lines.length && /^\s*>\s?/.test(lines[i])){ q.push(annInline(lines[i].replace(/^\s*>\s?/,""))); i++; }
      out.push("<blockquote>"+q.join("<br>")+"</blockquote>"); continue; }
    if(/^\s*([-*]|\d+\.)\s+/.test(ln)){ const ordered=/^\s*\d+\.\s+/.test(ln); const items=[];
      while(i<lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])){
        items.push("<li>"+annInline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/,""))+"</li>"); i++; }
      out.push((ordered?"<ol>":"<ul>")+items.join("")+(ordered?"</ol>":"</ul>")); continue; }
    if(!ln.trim()){ i++; continue; }
    const para=[];
    while(i<lines.length && lines[i].trim() && !annIsBlock(lines[i])){ para.push(annInline(lines[i])); i++; }
    out.push("<p>"+para.join("<br>")+"</p>");
  }
  return out.join("");
}

function Component({ canvas, props }) {
  const published = props.text || "";
  const [draft, setDraft] = React.useState(published);
  // Re-sync when the published text changes server-side (reload, another admin).
  React.useEffect(() => { setDraft(published); }, [published]);
  const dirty = draft !== published;

  function publish() { canvas.send({ action: "announce_set", text: draft }); }

  return (
    <div className="pc-ann">
      <div className="hd">
        <span>📣 Edit announcement</span>
        <span className={"st" + (dirty ? " dirty" : "")}>{dirty ? "● unpublished" : "✓ published"}</span>
      </div>
      <textarea className="ta" value={draft} spellCheck={false}
        placeholder="Write in Markdown…  # heading, **bold**, - list, [link](url)"
        onChange={e=>setDraft(e.target.value)}
        onKeyDown={e=>{ if((e.ctrlKey||e.metaKey) && e.key==="Enter") publish(); }} />
      <div className="row">
        <button className="btn" onClick={publish} disabled={!dirty}>Publish</button>
        <button className="btn ghost" onClick={()=>setDraft(published)} disabled={!dirty}>Revert</button>
        <span className="hint">Ctrl+Enter to publish</span>
      </div>
      <div className="prev-hd">Live preview</div>
      <div className="prev" dangerouslySetInnerHTML={{ __html: annMd(draft) }} />
    </div>
  );
}
