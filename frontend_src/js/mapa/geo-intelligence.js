const regions={N:["AC","AM","AP","PA","RO","RR","TO"],NE:["AL","BA","CE","MA","PB","PE","PI","RN","SE"],CO:["DF","GO","MS","MT"],SE:["ES","MG","RJ","SP"],S:["PR","RS","SC"]};
const allUF=Object.values(regions).flat().sort();
const fmt=new Intl.NumberFormat("pt-BR",{maximumFractionDigits:1});
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const quantity=(value,unit="")=>value==null?"Indisponível":fmt.format(value)+(unit?" "+unit:"");

function dailyChart(series){
  if(!series.length)return "<p class='geo-status'>Sem série diária para este período.</p>";
  const width=600,height=125,left=32,right=35,top=18,bottom=20,plot=height-top-bottom;
  const rainMax=Math.max(1,...series.map(r=>r.rain_mm??0)),ticketMax=Math.max(1,...series.map(r=>r.tickets));
  const step=(width-left-right)/series.length;
  const x=i=>left+(i+.5)*step,yRain=v=>top+plot*(1-v/rainMax),yTicket=v=>top+plot*(1-v/ticketMax);
  const bars=series.map((r,i)=>r.rain_mm==null?"":`<rect class="rain-bar" x="${x(i)-step*.32}" y="${yRain(r.rain_mm)}" width="${step*.64}" height="${plot*r.rain_mm/rainMax}"><title>${escape(r.date)}: chuva ${quantity(r.rain_mm,"mm")}; ${r.tickets} incidentes</title></rect>`).join("");
  const line=series.map((r,i)=>`${x(i)},${yTicket(r.tickets)}`).join(" ");
  const ticks=[0,Math.floor((series.length-1)/2),series.length-1].map(i=>`<text x="${x(i)}" y="${height-3}" text-anchor="middle">${series[i].date.slice(-2)}</text>`).join("");
  return `<div class="geo-chart-legend"><b>Chuva · mm (esquerda)</b><b>Incidentes · quantidade (direita)</b></div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Chuva estimada e incidentes diários. Eixo esquerdo em milímetros; direito em quantidade."><text x="0" y="${top+5}">${fmt.format(rainMax)}</text><text x="${width-right+5}" y="${top+5}">${fmt.format(ticketMax)}</text><text x="15" y="${top+plot}">0</text><text x="${width-right+5}" y="${top+plot}">0</text>${bars}<polyline class="ticket-line" points="${line}"/>${ticks}</svg>`;
}

export function mountGeoIntelligence({page,root,state,urlFor,refresh,onWeather,fitRegion,selectUF}){
  const panel=page.querySelector("[data-geo-intelligence]");
  if(!panel)return {update(){}};
  const $=s=>page.querySelector(s),region=$("[data-region-filter]"),uf=$("[data-geo-uf]"),month=$("[data-geo-month]"),category=$("[data-category-filter]"),layer=$("[data-geo-layer]");
  let controller=null,sequence=0,pollTimer=0;
  const status=$("[data-weather-status]");
  function syncControls(){
    region.value=state.region;
    const ufs=regions[state.region]||allUF;
    uf.innerHTML='<option value="">Todas as UFs</option>'+ufs.map(code=>`<option value="${code}">${code}</option>`).join("");
    uf.value=state.selectedUF;
    if(month){month.value=/^\d{4}-\d{2}$/.test(state.period)?state.period:state.period==="this_month"?root.dataset.currentPeriod:root.dataset.defaultPeriod;month.max=root.dataset.currentPeriod||"";}
    if(category)category.value=state.category||"";
    layer.value=state.layer;
  }
  async function update(preloaded){
    syncControls();
    const own=++sequence;
    controller?.abort();controller=new AbortController();
    const link=$("[data-present-action]");if(link)link.href=urlFor(link.getAttribute("href"),{period:state.period}).href;
    try{
      const response=preloaded?null:await fetch(urlFor(panel.dataset.url),{headers:{"X-Requested-With":"XMLHttpRequest"},signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)])});
      const payload=preloaded||await response.json();
      if(own!==sequence)return;
      if(response&&!response.ok)throw new Error(payload.error||"Análise mensal indisponível.");
      const climate=payload.climate,summary=payload.summary;
      $("[data-geo-context]").textContent=[state.selectedUF||region.options[region.selectedIndex]?.text||"Brasil",payload.period.month,payload.period.partial?"Período parcial":"Mês completo"].join(" · ");
      const metrics=[["Incidentes válidos",summary.ticket_count,""],["Links CMDB",summary.filtered_source_total,""],["Chuva estimada",climate.precipitation_mm,"mm"],["Dias chuvosos",climate.rainy_days_percent,"%"],["Vento máximo",climate.wind_max_kmh,"km/h"],["Rajada máxima",climate.gust_max_kmh,"km/h"]];
      $("[data-weather-metrics]").innerHTML=metrics.map(([label,value,unit])=>`<div><strong>${quantity(value,unit)}</strong><span>${escape(label)}${label==="Dias chuvosos"&&climate.rainy_days!=null?" · "+climate.rainy_days+" dias":""}</span></div>`).join("");
      $("[data-weather-chart]").innerHTML=dailyChart(payload.daily);
      const reasons=Object.entries(summary.category_counts||{}).sort((a,b)=>b[1]-a[1]);
      const projected=Object.entries(summary.distribution_projected||{}).filter(([,v])=>v>0);
      $("[data-geo-reasons]").innerHTML='<h3>Classificações registradas</h3>'+reasons.slice(0,5).map(([label,v])=>`<p><span>${escape(label)}</span><b>${fmt.format(v)}</b></p>`).join("")+(projected.length?'<div data-projected><h3>Distribuição projetada · estimativa</h3>'+projected.map(([label,v])=>`<p><span>${escape(label)}</span><b>${fmt.format(v)}</b></p>`).join("")+"<span>Não representa diagnóstico comprovado.</span></div>":"");
      const notes=[];
      if(climate.available){notes.push(`${climate.covered_ufs} UF(s) com cache · ${climate.points} células · completude ${quantity(climate.completeness*100,"%")}`);if(climate.stale)notes.push("Cache vencido");if(climate.partial)notes.push("Cobertura temporal parcial");}
      else notes.push("Clima ainda não coletado; indicadores operacionais disponíveis.");
      if(payload.missing_ufs.length)notes.push("Sem clima: "+payload.missing_ufs.join(", "));
      status.textContent=notes.join(" · ");
      onWeather(payload);
    }catch(error){if(error.name!=="AbortError"&&own===sequence)status.textContent=error.message;}
  }
  const changed=()=>refresh().catch(()=>{status.textContent="Não foi possível atualizar este recorte.";});
  region.addEventListener("change",()=>{state.region=region.value;state.selectedUF="";syncControls();changed();});
  uf.addEventListener("change",()=>selectUF(uf.value));
  month?.addEventListener("change",()=>{if(!month.value||!month.reportValidity())return;state.period=month.value;state.dateFrom="";state.dateTo="";changed();});
  category?.addEventListener("change",()=>{state.category=category.value;changed();});
  layer.addEventListener("change",()=>{state.layer=layer.value;if(state.layer==="calls"||state.layer==="motives"||["rain","wind","correlation"].includes(state.layer))state.mode="calls";changed();});
  $("[data-weather-refresh]").addEventListener("click",changed);
  $("[data-weather-toggle]").addEventListener("click",event=>{const content=$("#geo-intelligence-content"),open=content.hidden;content.hidden=!open;event.currentTarget.setAttribute("aria-expanded",String(open));event.currentTarget.textContent=open?"Recolher":"Expandir";window.dispatchEvent(new Event("resize"));});
  $("[data-geo-fullscreen]")?.addEventListener("click",async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{status.textContent="Tela cheia indisponível neste navegador.";}});
  $("[data-geo-collect-form]")?.addEventListener("submit",async event=>{
    event.preventDefault();const button=$("[data-weather-collect]");button.disabled=true;
    try{
      const form=event.currentTarget,body=new URLSearchParams(urlFor(panel.dataset.url).searchParams),csrf=form.querySelector("[name=csrfmiddlewaretoken]").value;
      body.set("csrfmiddlewaretoken",csrf);
      const response=await fetch(panel.dataset.collectUrl,{method:"POST",body,headers:{"X-CSRFToken":csrf}});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||"Não foi possível solicitar a coleta.");
      status.textContent=result.jobs?"Coleta na fila do worker. A análise continuará disponível; atualize para acompanhar.":"Cache válido já disponível.";
      clearTimeout(pollTimer);pollTimer=setTimeout(()=>update(),15000);
    }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
  });
  syncControls();
  return {update,allowedUFs:()=>regions[state.region]||allUF};
}
