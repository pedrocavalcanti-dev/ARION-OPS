const client=document.querySelector('[name="client"]');
const project=document.querySelector('[name="project"]');
const catalog=JSON.parse(document.getElementById('presentation-projects')?.textContent||'{}');
client?.addEventListener('change',()=>{
  project.replaceChildren(new Option('Todos os projetos',''),...(catalog[client.value]||[]).map(name=>new Option(name,name)));
});
