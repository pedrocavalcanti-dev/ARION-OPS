const regions={N:["AC","AM","AP","PA","RO","RR","TO"],NE:["AL","BA","CE","MA","PB","PE","PI","RN","SE"],CO:["DF","GO","MS","MT"],SE:["ES","MG","RJ","SP"],S:["PR","RS","SC"]};
const allUF=Object.values(regions).flat().sort();
const fmt=new Intl.NumberFormat("pt-BR",{maximumFractionDigits:1});
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const quantity=(value,unit="")=>value==null?"Indisponível":(typeof value==="number"?fmt.format(value):escape(value))+(unit?" "+unit:"");

function dailyChart(series, layer="rain"){
  if(!series.length)return "<p class='geo-status'>Sem série diária para este período.</p>";
  const width=600,height=125,left=32,right=35,top=18,bottom=20,plot=height-top-bottom;
  const config={temperature:["temperature_mean_c","Temperatura média","°C"],temperature_min:["temperature_min_c","Temperatura mínima","°C"],temperature_max:["temperature_max_c","Temperatura máxima","°C"],wind:["gust_max_kmh","Rajada máxima","km/h"],wind_speed:["wind_max_kmh","Vento máximo","km/h"]}[layer]||["rain_mm","Chuva estimada","mm"];
  const [key,label,unit]=config;
  const combined=layer==="correlation";
  if(combined)series=series.map(r=>({...r,tickets:r.technical_tickets}));
  const observed=series.map(r=>r[key]).filter(Number.isFinite),hasClimate=observed.length>0;
  const low=Math.min(0,...observed),high=Math.max(1,...observed);
  series=series.map(r=>({...r,rain_mm:r[key]==null?null:r[key]-low}));
  const rainMax=high-low,ticketMax=Math.max(1,...series.map(r=>r.tickets));
  const step=(width-left-right)/series.length;
  const x=i=>left+(i+.5)*step,yRain=v=>top+plot*(1-v/rainMax),yTicket=v=>top+plot*(1-v/ticketMax);
  const bars=series.map((r,i)=>r.rain_mm==null?"":`<rect class="rain-bar" x="${x(i)-step*.32}" y="${yRain(r.rain_mm)}" width="${step*.64}" height="${plot*r.rain_mm/rainMax}"><title>${escape(r.date)}: ${escape(label)} ${quantity(r.rain_mm+low,unit)}; ${r.tickets} incidentes</title></rect>`).join("");
  const line=series.map((r,i)=>`${x(i)},${yTicket(r.tickets)}`).join(" ");
  const ticks=[0,Math.floor((series.length-1)/2),series.length-1].map(i=>`<text x="${x(i)}" y="${height-3}" text-anchor="middle">${series.length>31?series[i].date.slice(8)+"/"+series[i].date.slice(5,7):series[i].date.slice(-2)}</text>`).join("");
  return `<div class="geo-chart-legend"><b>${hasClimate?escape(label)+" · "+unit+" (esquerda)":"Clima indisponível · sem simulação"}</b><b>Incidentes · quantidade (direita)</b></div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${hasClimate?escape(label)+' em '+unit+' e incidentes diários':'Incidentes diários; clima indisponível'}"><text x="0" y="${top+5}">${hasClimate?fmt.format(high):""}</text><text x="${width-right+5}" y="${top+5}">${fmt.format(ticketMax)}</text><text x="15" y="${top+plot}">${hasClimate?fmt.format(low):""}</text><text x="${width-right+5}" y="${top+plot}">0</text>${bars}<polyline class="ticket-line" points="${line}"/>${ticks}</svg>`;
}

export function mountGeoIntelligence({page,root,state,urlFor,refresh,onWeather,fitRegion,selectUF}){
  const panel=page.querySelector("[data-geo-intelligence]");
  if(!panel)return {update(){},syncMode(){},allowedUFs:()=>allUF};
  const $=s=>page.querySelector(s),region=$("[data-region-filter]"),uf=$("[data-geo-uf]"),month=$("[data-geo-month]"),category=$("[data-category-filter]"),layer=$("[data-geo-layer]");
  let controller=null,sequence=0,pollTimer=0,timelineTimer=0,current=null;
  let pendingPlanToken="";
  const dayInput=$("[data-timeline-day]"),dayLabel=$("[data-timeline-label]"),play=$("[data-timeline-play]");
  const categoryKeys={Massiva:"massiva",Rompimento:"rompimento",Equipamento:"equipamento",Energia:"energia","Configuração":"configuracao","Reset remoto":"reset_remoto","Demais motivos técnicos":"demais","Sem classificação":"sem_classificacao","Pendência Financeira":"financeiro"};
  const status=$("[data-weather-status]");
  const timeline=$("[data-geo-timeline]");
  function setPanelState(value){
    panel.dataset.panelState=value;
    page.classList.toggle("has-analytics-drawer",value==="expanded");
    page.dispatchEvent(new CustomEvent("geo:analysis",{detail:{expanded:value==="expanded"}}));
    const open=value!=="collapsed",expanded=value==="expanded";
    $("#geo-analysis-body").hidden=!open;
    $("#geo-intelligence-content").hidden=!expanded;
    $("[data-analysis-expand]").hidden=!open;
    $("[data-analysis-close]").hidden=!open;
    $("[data-weather-toggle]").setAttribute("aria-expanded",String(open));
    $("[data-weather-toggle]").textContent=open?"Recolher":"Análises";
    $("[data-analysis-expand]").textContent=expanded?"Resumo":"Expandir";
    $("[data-analysis-expand]").setAttribute("aria-expanded",String(expanded));
    $("[data-timeline-expand]").setAttribute("aria-expanded",String(expanded));
    $("[data-timeline-expand]").textContent=expanded?"Recolher":"Detalhar";
    page.dispatchEvent(new CustomEvent("geo:layout"));
  }
  if(window.ResizeObserver)new ResizeObserver(()=>{
    page.style.setProperty("--geo-analytics-height",`${panel.getBoundingClientRect().height}px`);
    page.dispatchEvent(new CustomEvent("geo:layout"));
  }).observe(panel);
  function syncControls(){
    region.value=state.region;
    const ufs=current?.summary?.presentation?current.summary.allowed_ufs:(regions[state.region]||allUF);
    if(current?.summary?.presentation){
      region.innerHTML='<option value="">Brasil</option>'+current.catalog.regions.map(r=>`<option value="${r.sigla}">${escape(r.nome)}</option>`).join("");region.value=state.region;
    }
    uf.innerHTML='<option value="">Todas as UFs</option>'+ufs.map(code=>`<option value="${code}">${code}</option>`).join("");
    uf.value=state.selectedUF;
    if(month){month.value=/^\d{4}-\d{2}$/.test(state.period)?state.period:state.period==="this_month"?root.dataset.currentPeriod:root.dataset.defaultPeriod;month.max=root.dataset.currentPeriod||"";}
    if(category)category.value=state.category||"";
    layer.value=state.layer;
  }
  async function update(preloaded){
    pendingPlanToken="";
    const collectButton=$("[data-weather-collect]");if(collectButton)collectButton.textContent="Coletar clima do recorte";
    syncControls();
    const own=++sequence;
    controller?.abort();controller=new AbortController();
    const link=document.querySelector("[data-present-action]");if(link)link.href=urlFor(link.getAttribute("href"),{period:state.period}).href;
    try{
      const response=preloaded?null:await fetch(urlFor(panel.dataset.url),{headers:{"X-Requested-With":"XMLHttpRequest"},signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)])});
      const payload=preloaded||await response.json();
      if(own!==sequence)return;
      if(response&&!response.ok)throw new Error(payload.error?.message||payload.error||"Análise mensal indisponível.");
      current=payload;syncControls();pause();
      syncMode();
      if(state.day&&!payload.daily.some(r=>r.date===state.day))state.day="";
      dayInput.max=payload.daily.length;
      dayInput.value=state.day?payload.daily.findIndex(r=>r.date===state.day)+1:0;
      render();
    }catch(error){if(error.name!=="AbortError"&&own===sequence)status.textContent=error.message;}
  }
  function render(){
      const payload=current;if(!payload)return;
      const climate=state.day?payload.daily.find(r=>r.date===state.day)||{}:payload.climate,summary=payload.summary;
      dayLabel.textContent=state.day?state.day.slice(8)+"/"+state.day.slice(5,7):"Mês completo";
      $("[data-geo-context]").textContent=[state.selectedUF||region.options[region.selectedIndex]?.text||"Brasil",payload.period.month,payload.period.partial?"Período parcial":"Mês completo"].join(" · ");
      const reasons=Object.entries(summary.category_counts||{}).filter(([label])=>label!=="Sem classificação").sort((a,b)=>b[1]-a[1]);
      const incidents=state.day?(climate.tickets??0):summary.ticket_count;
      const distinct=field=>new Set(Object.values(summary.states).flatMap(r=>Object.keys(r[field]||{})).filter(v=>!v.includes("não identificado"))).size;
      const stateRanking=Object.entries(summary.states).filter(([,r])=>r.ticket_count>0).sort((a,b)=>b[1].ticket_count-a[1].ticket_count);
      const peak=payload.daily.reduce((best,r)=>r.tickets>(best?.tickets||0)?r:best,null);
      const metrics=state.mode==="climate"?[[payload.climate.partial?"Chuva · recorte coberto":"Chuva estimada",state.day?climate.rain_mm:climate.precipitation_mm,"mm"],[state.day?"Incidentes no dia":"Dias chuvosos",state.day?incidents:climate.rainy_days,""],["Temperatura média",climate.temperature_mean_c,"°C"],["Rajada máxima",climate.gust_max_kmh,"km/h"]]:["base","projects"].includes(state.mode)?[["Links CMDB",summary.filtered_source_total,""],["UFs com links",Object.values(summary.states).filter(r=>r.total_links>0).length,""],["Projetos",distinct("project_counts"),""],["Provedores",distinct("provider_counts"),""]]:state.mode==="motives"?[["Categoria predominante",reasons[0]?.[0]||"Sem classificação",""],["UF com mais incidentes",stateRanking[0]?.[0]||"Sem dados",""],["Dia de maior concentração",peak?peak.date.slice(8)+"/"+peak.date.slice(5,7):"Sem dados",""],["Dias coincidentes com chuva",payload.climate.coincident_days," dias"]]:[[state.day?"Incidentes no dia":"Incidentes válidos",incidents,""],["Links afetados no mês",summary.affected_link_count,""],["Links reincidentes no mês",summary.recurrence_link_count,""],["Motivo predominante",reasons[0]?.[0]||"Sem classificação",""]];
      $("[data-weather-metrics]").innerHTML=metrics.map(([label,value,unit])=>`<div><strong>${quantity(value,unit)}</strong><span>${escape(label)}${label==="Dias chuvosos"&&climate.rainy_days!=null?" · "+climate.rainy_days+" dias":""}</span></div>`).join("");
      $("[data-weather-chart]").innerHTML=payload.comparison_time_compatible===false&&payload.climate.available?'<p class="geo-status">Referência climática UTC. Comparação diária com incidentes em horário local indisponível; consulte os indicadores e as UFs separadamente.</p>':dailyChart(payload.daily,state.layer);
      const projected=Object.entries(summary.distribution_projected||{}).filter(([,v])=>v>0);
      const projectedTotal=projected.reduce((n,[,v])=>n+v,0);
      $("[data-geo-reasons]").innerHTML='<h3>Classificações registradas · mês</h3>'+reasons.slice(0,5).map(([label,v])=>`<button type="button" data-explore-category="${categoryKeys[label]||""}" title="Explorar esta classificação no mapa"><span>${escape(label)}</span><b>${fmt.format(v)}</b></button>`).join("")+(projected.length?'<span>Cenário estimado · distribuição por pesos</span><div class="geo-scenario" aria-label="Cenário estimado; não representa diagnóstico confirmado">'+projected.map(([label,v])=>`<span style="flex:${v}" title="${escape(label)}: ${v} estimados (${quantity(v/projectedTotal*100)}%). Não é diagnóstico registrado."></span>`).join("")+"</div>":"");
      if(["base","projects"].includes(state.mode)){
        const counts={};Object.values(summary.states).forEach(r=>Object.entries(r.project_counts||{}).forEach(([name,n])=>counts[name]=(counts[name]||0)+n));
        $("[data-weather-chart]").innerHTML='<h3>Projetos · links CMDB atuais</h3>'+Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([name,n])=>`<p class="geo-analysis-row"><span>${escape(name)}</span><b>${fmt.format(n)}</b></p>`).join("");
        const regionLinks=Object.fromEntries(Object.keys(regions).map(r=>[r,regions[r].reduce((n,uf)=>n+(summary.states[uf]?.total_links||0),0)]));
        $("[data-geo-reasons]").innerHTML='<h3>Links CMDB por região</h3>'+Object.entries(regionLinks).filter(([,n])=>n>0).map(([label,n])=>`<p><span>${label}</span><b>${fmt.format(n)}</b></p>`).join("");
      }else{
        $("[data-geo-reasons]").insertAdjacentHTML("beforeend",'<h3>Incidentes por UF · mês</h3>'+stateRanking.slice(0,5).map(([code,r])=>`<p><span>${code}</span><b>${fmt.format(r.ticket_count)}</b></p>`).join(""));
      }
      const notes=[];
      if(payload.climate.source)notes.push(`${payload.climate.source} · ${payload.climate.timezone||"referências distintas"}`);
      if(payload.climate.coverage_label)notes.push(`${payload.climate.coverage_label} · área amostrada coberta ${quantity(payload.climate.territorial_percent,"%")}`);
      if(payload.climate.available){const climate=payload.climate;notes.push(`${climate.covered_ufs} UF(s) com cache · ${climate.points} células · completude ${quantity(climate.completeness*100,"%")}`);if(climate.stale)notes.push("Cache vencido");if(climate.temporal_partial)notes.push("Cobertura temporal parcial");}
      if(payload.collection?.message)notes.push(payload.collection.message);
      else if(!payload.climate.available)notes.push("Dados climáticos indisponíveis.");
      if(payload.collection?.requests_today!=null)notes.push(`${payload.collection.requests_today}/${payload.collection.daily_budget} HTTP hoje · orçamento interno`);
      if(payload.collection?.checkpoint_cells)notes.push(`${payload.collection.checkpoint_cells} células em progresso`);
      if(payload.collection?.jobs){const jobs=payload.collection.jobs;notes.push(`${jobs.pending} pendentes · ${jobs.running} em processamento · ${jobs.error} com erro · ${jobs.paused} pausadas`);}
      if(payload.climate.available&&payload.climate.last_update)notes.push(`${payload.climate.method} · atualização ${new Date(payload.climate.last_update).toLocaleString("pt-BR")}`);
      if(payload.missing_ufs.length&&payload.climate.available)notes.push(`${payload.missing_ufs.length} UF(s) sem cache`);
      status.textContent=notes.join(" · ");
      status.dataset.state=payload.collection?.configuration_status==="not_configured"?"not_configured":payload.collection?.status||"unavailable";
      const canCollect=Boolean(payload.collection?.can_collect),busy=["queued","running"].includes(payload.collection?.status);
      const form=$("[data-geo-collect-form]");if(form)form.hidden=!canCollect||busy;
      const configure=$("[data-weather-configure]");if(configure)configure.hidden=payload.collection?.configuration_status==="ready";
      clearTimeout(pollTimer);
      if(busy)pollTimer=setTimeout(()=>update(),15000);
      const q=summary.reconciliation;
      $("[data-geo-quality]").textContent=q?`${q.incidents} incidentes distintos · ${q.classified} classificados · ${q.unclassified} sem classificação confiável · ${q.with_uf} com UF · ${q.without_uf} sem localização vinculada · ${q.affected_links} links afetados distintos · ${q.recurrent_links} reincidentes. Ocorrências estaduais: ${q.state_occurrences}; não somar para obter o total nacional.`:payload.notice;
      if(payload.missing_ufs.length)$("[data-geo-quality]").append(document.createTextNode(" Sem clima: "+payload.missing_ufs.join(", ")+"."));
      if(payload.climate.available){const c=payload.climate;$("[data-geo-quality]").append(document.createTextNode(` Chuva elevada: ${quantity(c.heavy_rain_days)} dias com média territorial ≥ 20 mm. Coincidência: ${quantity(c.coincident_days)} dias com chuva ≥ 1 mm e incidentes técnicos; não confirma causa. Temperatura: ${quantity(c.temperature_min_c,"°C")} a ${quantity(c.temperature_max_c,"°C")}. Vento máximo: ${quantity(c.wind_max_kmh,"km/h")}; direção média aproximada: ${quantity(c.wind_direction_deg,"°")}.`));}
      onWeather(payload,state.day);
      renderClimateExtras(payload);
      const fields={rain:"precipitation_sum",temperature:"temperature_2m_mean",temperature_min:"temperature_2m_min",temperature_max:"temperature_2m_max",wind:"wind_gusts_10m_max",wind_speed:"wind_speed_10m_max",direction:"wind_direction_10m_dominant",correlation:"precipitation_sum"};
      for(const option of layer.options){const field=fields[option.value];if(!field)continue;option.disabled=!Object.values(payload.weather).some(row=>row.daily.some(day=>Number.isFinite(day[field])));option.title=option.disabled?"Variável sem observações neste recorte; ausência não equivale a zero.":"";}
  }
  function renderClimateExtras(payload){
    const ranking=$("[data-weather-ranking]"),select=$("[data-climate-ranking]"),context=$("[data-weather-correlation]"),localities=$("[data-weather-localities]");
    const rows=payload.rankings?.[select.value]||[];
    ranking.innerHTML='<p class="geo-status">Somente UFs com cobertura completa e cache vigente no período; posições entre os recortes disponíveis.</p>'+rows.slice(0,27).map((row,i)=>`<button class="geo-analysis-row" type="button" data-climate-uf="${row.uf}"><span>${i+1}. ${row.uf}</span><b>${quantity(row.value)}</b></button>`).join("")+(!rows.length?'<p class="geo-status">Sem recortes comparáveis neste intervalo.</p>':"");
    const c=payload.correlation||{};
    context.innerHTML=`<p class="geo-status">${c.eligible_tickets??0} tickets técnicos elegíveis · ${c.located_tickets??0} com município CMDB · ${c.covered_tickets??0} com cobertura horária local · ${c.daily_covered??0} com cobertura diária municipal. ${c.insufficient??0} sem dados suficientes.</p>`+
      [["Chamados técnicos em dias chuvosos",c.rain_days],["Chamados em janela chuvosa",c.rain],["Rompimentos com chuva",c.ruptures_rain],["Massivas com precipitação",c.massive_rain],["Equipamento / energia durante evento",c.equipment_weather],["Links com vento intenso",c.wind_links],["Localidades com chuva e incidentes",c.rain_localities],["Coincidência temporal e geográfica",c.coincidence],["Evidência contextual relevante",c.contextual],["Sem coincidência identificada",c.none]].map(([name,n])=>`<p class="geo-analysis-row"><span>${name}</span><b>${quantity(n)}</b></p>`).join("")+`<p class="geo-status">${escape(c.definition||payload.notice)} ${escape(c.time_basis||"")}</p>`;
    const regions=c.region_rain_incidents||[];
    context.insertAdjacentHTML("beforeend",regions.sort((a,b)=>b.tickets-a.tickets).map(row=>`<p class="geo-analysis-row"><span>${escape(payload.catalog.regions.find(r=>r.id===row.region_id)?.nome)} · incidentes em janela chuvosa</span><b>${row.tickets}</b></p>`).join(""));
    const available=c.localities||[];
    const cities=new Map(available.map(row=>[row.uf+":"+row.city.toLocaleLowerCase("pt-BR"),row]));
    if(state.selectedUF&&!root.dataset.presentation?.includes("true")){
      const counts=payload.summary.states[state.selectedUF]?.city_counts||{};
      Object.entries(counts).forEach(([key,value])=>{const city=typeof value==="object"?Object.keys(value.variants||{})[0]||key:key;const id=state.selectedUF+":"+city.toLocaleLowerCase("pt-BR");if(!cities.has(id))cities.set(id,{city,uf:state.selectedUF,incidents:0,available:false});});
    }
    const list=[...cities.values()].filter(row=>!state.selectedUF||row.uf===state.selectedUF).sort((a,b)=>(b.rain_incidents||0)-(a.rain_incidents||0)||b.incidents-a.incidents);
    localities.innerHTML='<h3>Localidades · cache sob demanda</h3>'+list.slice(0,80).map(row=>`<div class="geo-locality-row"><span>${escape(row.city)} / ${row.uf}<small>${row.incidents} incidentes técnicos · ${row.available?"cache municipal disponível":"sem cache municipal"}</small></span>${panel.dataset.localUrl?`<button type="button" class="geo-text-action" data-local-city="${escape(row.city)}" data-local-uf="${row.uf}" data-local-action="${row.available?"view":"collect"}">${row.available?"Investigar":"Coletar local"}</button>`:""}</div>`).join("")+(!list.length?'<p class="geo-status">Selecione uma UF para explorar os municípios da CMDB. Sem localização municipal não há coincidência local.</p>':"");
    for(const item of [ranking.closest("details"),context.closest("details")])item.hidden=state.mode!=="climate";
  }
  $("[data-climate-ranking]")?.addEventListener("change",()=>current&&renderClimateExtras(current));
  $("[data-weather-ranking]")?.addEventListener("click",event=>{const row=event.target.closest("[data-climate-uf]");if(row)selectUF(row.dataset.climateUf);});
  let localContext=null;
  async function localRequest(city,uf,collect=false,ticket=""){
    const result=$("[data-local-weather-result]");result.textContent=collect?"Solicitando coleta municipal ao Worker…":"Consultando cache municipal…";
    if(!/^\d{4}-\d{2}$/.test(current?.period.month||"")){result.textContent="Selecione um mês para investigar a localidade.";return;}
    const url=urlFor(panel.dataset.localUrl,{city,uf,period:current.period.month,ticket});
    const csrf=page.querySelector("[name=csrfmiddlewaretoken]")?.value;
    try{
      const response=await fetch(url,{method:collect?"POST":"GET",...(collect?{body:new URLSearchParams(url.searchParams),headers:{"X-CSRFToken":csrf||""}}:{})});
      const data=await response.json();if(!response.ok)throw new Error(data.error?.message||data.error||"Consulta local indisponível.");
      localContext={city,uf};
      result.innerHTML=`<h3>${escape(city)} / ${uf}</h3><p class="geo-status">${escape(({success:"Concluído",pending:"Na fila",running:"Processando",error:"Falhou",unavailable:"Sem cache"})[data.status]||data.status)} · ${escape(data.precision)} ${escape(data.last_error||"")}</p>`;
      if(data.weather){result.insertAdjacentHTML("beforeend",`<p class="geo-status">${quantity(data.weather.precipitation_mm,"mm")} · ${quantity(data.weather.temperature_mean_c,"°C")} · rajada ${quantity(data.weather.gust_max_kmh,"km/h")}. ${escape(data.source)} · IBGE ${escape(data.ibge_code)}.</p><form data-local-window-form><label>TicketID OTRS <input inputmode="numeric" pattern="[0-9]+" name="ticket" required aria-label="TicketID real para investigar janela horária"></label><button class="geo-text-action">Investigar janela horária</button></form>`);}
      if(data.window){result.insertAdjacentHTML("beforeend",`<p class="geo-status">${escape(({insufficient:"Sem dados suficientes",none:"Sem coincidência identificada",coincidence:"Coincidência temporal e geográfica",contextual:"Evidência contextual relevante"})[data.window.state]||data.window.state)} · ${escape(data.window.time_basis)} Coincidência não confirma causa.</p>`+data.window.hourly.map(row=>`<p class="geo-analysis-row"><span>${escape(row.at.slice(0,16))} UTC</span><b>${quantity(row.precipitation,"mm")} · ${quantity(row.wind_gusts_10m,"km/h")}</b></p>`).join(""));}
    }catch(error){result.textContent=error.message;}
  }
  $("[data-weather-localities]")?.addEventListener("click",event=>{const b=event.target.closest("[data-local-city]");if(b)localRequest(b.dataset.localCity,b.dataset.localUf,b.dataset.localAction==="collect");});
  $("[data-local-weather-result]")?.addEventListener("submit",event=>{if(!event.target.matches("[data-local-window-form]")||!localContext)return;event.preventDefault();localRequest(localContext.city,localContext.uf,false,new FormData(event.target).get("ticket"));});
  const changed=()=>refresh().catch(()=>{status.textContent="Não foi possível atualizar este recorte.";});
  region.addEventListener("change",()=>{state.region=region.value;if(state.selectedUF&&!(regions[state.region]||allUF).includes(state.selectedUF)){state.selectedUF="";$("[data-filter-notice]").textContent="A UF anterior foi removida porque não pertence à região selecionada.";}syncControls();changed();});
  uf.addEventListener("change",()=>selectUF(uf.value));
  month?.addEventListener("change",()=>{if(!month.value||!month.reportValidity())return;state.period=month.value;state.dateFrom="";state.dateTo="";changed();});
  category?.addEventListener("change",()=>{state.category=category.value;changed();});
  layer.addEventListener("change",()=>{state.layer=layer.value;if(["rain","wind","wind_speed","temperature","temperature_min","temperature_max","direction","correlation"].includes(state.layer))state.mode="climate";else if(state.layer==="calls"||state.layer==="motives")state.mode=state.layer;changed();});
  $("[data-weather-refresh]").addEventListener("click",changed);
  $("[data-weather-toggle]").addEventListener("click",()=>setPanelState(panel.dataset.panelState==="collapsed"?"summary":"collapsed"));
  $("[data-analysis-expand]").addEventListener("click",()=>setPanelState(panel.dataset.panelState==="expanded"?"summary":"expanded"));
  $("[data-analysis-close]").addEventListener("click",()=>{setPanelState("collapsed");$("[data-weather-toggle]").focus();});
  $("[data-timeline-expand]").addEventListener("click",()=>setPanelState(panel.dataset.panelState==="expanded"?"summary":"expanded"));
  $("[data-timeline-reset]").addEventListener("click",()=>{pause();dayInput.value=0;showDay();});
  $("[data-geo-reasons]").addEventListener("click",event=>{const button=event.target.closest("[data-explore-category]");if(!button)return;state.category=button.dataset.exploreCategory;state.mode="calls";state.layer="calls";changed();});
  const pause=()=>{clearInterval(timelineTimer);timelineTimer=0;play.textContent="▶";play.setAttribute("aria-label","Reproduzir dias");};
  function syncMode(){
    if(month){month.disabled=state.mode==="base";month.title=month.disabled?"Base representa o inventário CMDB atual; período não altera os links.":"";}
    const icons=$("[data-weather-icons]"),iconType=$("[data-weather-icon-type]");
    for(const control of [icons,iconType])if(control){control.disabled=state.mode!=="climate";control.title=state.mode==="climate"?"":"Disponível no modo Clima.";}
    timeline.hidden=!["calls","motives","climate"].includes(state.mode);
    page.classList.toggle("has-timeline",!timeline.hidden);
    if(timeline.hidden){pause();state.day="";}
  }
  function showDay(){state.day=current?.daily[Number(dayInput.value)-1]?.date||"";render();}
  dayInput.addEventListener("input",()=>{pause();showDay();});
  function start(){pause();if(!current)return;play.textContent="Ⅱ";play.setAttribute("aria-label","Pausar reprodução");timelineTimer=setInterval(()=>{dayInput.value=Number(dayInput.value)>=Number(dayInput.max)?1:Number(dayInput.value)+1;showDay();},Number($("[data-timeline-speed]").value));}
  play.addEventListener("click",()=>timelineTimer?pause():start());
  $("[data-timeline-speed]").addEventListener("change",()=>{if(timelineTimer)start();});
  document.addEventListener("visibilitychange",()=>{if(document.hidden)pause();});
  $("[data-geo-fullscreen]")?.addEventListener("click",async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{status.textContent="Tela cheia indisponível neste navegador.";}});
  $("[data-geo-collect-form]")?.addEventListener("submit",async event=>{
    event.preventDefault();const button=$("[data-weather-collect]");button.disabled=true;
    try{
      const form=event.currentTarget,body=new URLSearchParams(urlFor(panel.dataset.url).searchParams),csrf=form.querySelector("[name=csrfmiddlewaretoken]").value;
      body.set("csrfmiddlewaretoken",csrf);
      if(pendingPlanToken)body.set("preview_token",pendingPlanToken);
      const response=await fetch(panel.dataset.collectUrl,{method:"POST",body,headers:{"X-CSRFToken":csrf}});
      const result=await response.json().catch(()=>({message:response.status===403?"Sem autorização ou sessão CSRF expirada. Recarregue a página.":"Resposta indisponível. Consulte a integração."}));
      status.dataset.state=result.status||"unavailable";
      if(!response.ok)throw new Error(result.message||result.error?.message||result.error||"Não foi possível solicitar a coleta.");
      status.textContent=result.message;
      if(result.status==="preview"){pendingPlanToken=result.preview_token;button.textContent=`Autorizar lote · ${result.estimated_requests} requisições`;return;}
      await update();
    }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
  });
  syncControls();
  return {update,syncMode,collapse:()=>{if(panel.dataset.panelState==="expanded")setPanelState("summary");},allowedUFs:()=>current?.summary?.presentation?current.summary.allowed_ufs:(regions[state.region]||allUF)};
}
