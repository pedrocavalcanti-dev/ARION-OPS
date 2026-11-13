import { mountGeoIntelligence } from "./geo-intelligence.js";
import { mountWeatherIcons } from "./weather-icons.js";
import { CAUSE_ORDER, causeColor, causeBadge } from "./cause-meta.js";
(() => {
  const root = document.querySelector("[data-map-config]");
  const page = document.querySelector(".geo-page");
  if (!root || !page) return;
  const $ = selector => (page || root).querySelector(selector);
  const number = new Intl.NumberFormat("pt-BR");
  const stateNames = { AC:"Acre",AL:"Alagoas",AM:"Amazonas",AP:"Amapá",BA:"Bahia",CE:"Ceará",DF:"Distrito Federal",ES:"Espírito Santo",GO:"Goiás",MA:"Maranhão",MG:"Minas Gerais",MS:"Mato Grosso do Sul",MT:"Mato Grosso",PA:"Pará",PB:"Paraíba",PE:"Pernambuco",PI:"Piauí",PR:"Paraná",RJ:"Rio de Janeiro",RN:"Rio Grande do Norte",RO:"Rondônia",RR:"Roraima",RS:"Rio Grande do Sul",SC:"Santa Catarina",SE:"Sergipe",SP:"São Paulo",TO:"Tocantins" };
  const modeLabels = { climate:"Clima estimado", base:"Links CMDB",projects:"Links do projeto",calls:"Tickets criados no período",repeat:"Links reincidentes",motives:"Incidentes por motivo" };
  const brandPalettes = {
    light: {
      claro:["#f0d5cf","#d18b80","#a95047"], vivo:["#ece6f3","#a38bbc","#665087"],
      algar:["#e3eee4","#91b39a","#47745d"], other:["#e4ebee","#95aab4","#5d7581"], unidentified:"#c7ced0",
    },
    dark: {
      claro:["#3b2c2a","#9e5d55","#cc8980"], vivo:["#30293a","#76618f","#b6a0cc"],
      algar:["#29372e","#668a71","#a1bea8"], other:["#29343a","#657c87","#a8bbc3"], unidentified:"#515a5d",
    },
  };
  const quantitative = {
    projects:{dark:["#202a2d","#526e78","#8eabb3"],light:["#e5ebed","#8ca3ad","#3d6472"]},
    calls:{dark:["#2c2521","#96664e","#d39a79"],light:["#f2e8de","#e2a47e","#b5573b"]},
    repeat:{dark:["#2d242d","#75435f","#c181a4"],light:["#f0e5ed","#c17fa3","#773d65"]},
    motives:{dark:["#2c281f","#a47734","#e3a251"],light:["#f4edda","#e4aa4c","#aa4733"]},
  };
  const projectPalettes = {
    default:quantitative.projects,
    diversos:quantitative.projects,
    bradesco:{dark:["#4a252c","#a44750","#e08087"],light:["#f4e1e3","#d46670","#8e2030"]},
    banco_do_brasil:{dark:["#423d24","#9b8536","#e7c447"],light:["#f7f0d2","#dbc559","#b09524"]},
    correios:{dark:["#324258","#3f7fb7","#eac249"],light:["#f3ecd2","#d5b342","#234c87"]},
    arcos_dourados:{dark:["#5c4323","#d98628","#c83138"],light:["#fff0c9","#e89c2e","#c21f32"]},
    caixa_economica_federal:{dark:["#293951","#4578ab","#eb913d"],light:["#e7eef8","#6489bb","#c05b29"]},
    fust:{dark:["#3b2d4c","#77569b","#b397d5"],light:["#eee7f5","#a27dc2","#5f3e87"]},
    escolas_conectadas:{dark:["#293a36","#498770","#a8cb73"],light:["#e6f0e8","#7aaa7a","#386747"]},
    carrefour:{dark:["#343c4b","#6f7194","#d55252"],light:["#e9eef5","#8299b3","#bb3047"]},
    petrobras:{dark:["#294237","#4b9867","#dbba45"],light:["#f3efd8","#d6bd45","#337451"]},
  };
  const projectPaletteAliases = {
    bdn_bradesco:"bradesco",
    projeto_bb:"banco_do_brasil",
    caixa_economica:"caixa_economica_federal",
    mcdonald_s:"arcos_dourados",
  };
  const stateLabelCoordinates = {
    AC:[-70.4,-9.1], AM:[-63.8,-4.2], AP:[-51.8,1.1], BA:[-41.5,-12.1], CE:[-39.4,-5.1], GO:[-49.5,-16.0],
    MA:[-45.1,-5.5], MG:[-44.5,-18.5], MS:[-54.6,-20.5], MT:[-56.2,-13.3], PA:[-52.0,-4.2],
    PI:[-42.7,-7.4], PR:[-51.0,-24.7], RO:[-63.0,-10.8], RS:[-53.0,-30.3], SC:[-50.5,-27.3],
    RR:[-61.5,2.1], SP:[-48.5,-22.3], TO:[-48.3,-10.4],
  };
  const smallStateCallouts = {
    RN:{anchor:[-36.4,-5.7],label:[-32.8,-4.8]}, PB:{anchor:[-36.7,-7.1],label:[-32.8,-7.2]},
    PE:{anchor:[-35.2,-8.4],label:[-32.8,-9.6]}, AL:{anchor:[-36.2,-9.6],label:[-32.8,-12]},
    SE:{anchor:[-37.2,-10.6],label:[-32.8,-14.4]}, ES:{anchor:[-40.5,-19.6],label:[-38.5,-19.4]},
    RJ:{anchor:[-42.2,-22.6],label:[-39,-22.3]}, DF:{anchor:[-47.9,-15.8],label:[-46.1,-15.8]},
  };
  const stateOrder = Object.keys(stateNames).sort((a,b)=>stateNames[a].localeCompare(stateNames[b],"pt-BR"));
  let weatherIcons=null,lastWeatherPayload=null;
  const required = [
    "#brazil-map", "#geo-title", "#state-flags-data", "[data-source-badge]", "[data-breadcrumb]", "[data-selected-name]",
    "[data-map-message]", "[data-map-error]", "[data-map-error-title]", "[data-map-error-detail]", "[data-map-retry]",
    "[data-national-panel]", "[data-national-view]", "[data-national-content]", "[data-national-collapse]", "[data-national-expand]",
    "[data-national-tab='summary']", "[data-national-tab='ranking']", "[data-national-tab='distribution']", "[data-brazil-link]",
    "[data-state-panel]", "[data-panel-backdrop]", "[data-panel-close]", "[data-uf-previous]", "[data-uf-next]", "[data-panel-code]", "[data-panel-name]", "[data-panel-flag]",
    "[data-panel-total]", "[data-panel-scope]", "[data-panel-context]", "[data-panel-overview]", "[data-panel-projects]", "[data-panel-providers]", "[data-panel-reasons]", "[data-panel-recurrence]", "[data-panel-tickets]", "[data-state-tab='overview']", "[data-state-tab='tickets']", "[data-panel-ticket-search]", "[data-panel-ticket-size]", "[data-panel-ticket-pagination]",
    "[data-ticket-filters]", "[data-status-filter]", "[data-priority-filter]",
    "[data-open-base]", "[data-open-cockpit]", "[data-map-announcement]", "[data-state-search]", "[data-state-search-results]",
    "[data-mode]", ...(root.dataset.presentation==="true"?[]:["[data-client]"]), "[data-project-filter]", "[data-period-control]", "[data-period-filter]",
    "[data-provider-filter]", "[data-motives-select]", "[data-motive-filter]", "[data-custom-period]", "[data-arion-status-filter]", "[data-assignee-filter]", "[data-active-filters]",
    "[data-date-from]", "[data-date-to]", "[data-apply-period]", "[data-legend]",
  ];
  const missingRequired = required.filter(selector => !$(selector));
  if (missingRequired.length) {
    console.error("Mapa: elementos obrigatorios ausentes no template.", missingRequired);
    return;
  }
  const mapElement = $("#brazil-map"), message = $("[data-map-message]"), errorBox = $("[data-map-error]");
  const panel = $("[data-state-panel]"), national = $("[data-national-panel]"), panelBackdrop = $("[data-panel-backdrop]");
  const flagsNode = $("#state-flags-data");
  const flags = flagsNode ? JSON.parse(flagsNode.textContent || "{}") : {};
  const ui = {
    source: $("[data-source-badge]"), breadcrumb: $("[data-breadcrumb]"), selectedName: $("[data-selected-name]"),
    project: $("[data-project-filter]"), period: $("[data-period-filter]"), periodControl: $("[data-period-control]"), provider: $("[data-provider-filter]"),
    reason: $("[data-motive-filter]"), reasonWrap: $("[data-motives-select]"), dates: $("[data-custom-period]"),
    dateFrom: $("[data-date-from]"), dateTo: $("[data-date-to]"), search: $("[data-state-search]"), results: $("[data-state-search-results]"),
    nationalView: $("[data-national-view]"), panelName: $("[data-panel-name]"), panelCode: $("[data-panel-code]"), panelFlag: $("[data-panel-flag]"),
    panelTotal: $("[data-panel-total]"), panelScope: $("[data-panel-scope]"), panelContext: $("[data-panel-context]"),
    clients: $("[data-panel-clients]"), statuses: $("[data-panel-status]"), projects: $("[data-panel-projects]"), providers: $("[data-panel-providers]"), cities: $("[data-panel-cities]"),
    reasons: $("[data-panel-reasons]"), links: $("[data-panel-links]"), tickets: $("[data-panel-tickets]"),
    ticketFilters: $("[data-ticket-filters]"), status: $("[data-status-filter]"), priority: $("[data-priority-filter]"), arionStatus: $("[data-arion-status-filter]"), assignee: $("[data-assignee-filter]"), activeFilters: $("[data-active-filters]"),
    moreFilters: $("[data-more-filters]"), moreFiltersContent: $("[data-more-filters-content]"), hoverTooltip: $("[data-state-hover-tooltip]"),
    panelOverview: $("[data-panel-overview]"), panelOverviewContext: $("[data-panel-overview-context]"), panelRecurrence: $("[data-panel-recurrence]"), panelTicketSearch: $("[data-panel-ticket-search]"), panelTicketSize: $("[data-panel-ticket-size]"), panelTicketPagination: $("[data-panel-ticket-pagination]"),
    openBase: $("[data-open-base]"), openCockpit: $("[data-open-cockpit]"), ufPrevious: $("[data-uf-previous]"), ufNext: $("[data-uf-next]"), announce: $("[data-map-announcement]"), legend: $("[data-legend]"),
  };
  const state = { mode:"base", client:"all", project:"", provider:"", period:root.dataset.defaultPeriod||"last_month", region:"", layer:"analysis", category:"", reason:"", status:"", priority:"", arionStatus:"", assignee:"", dateFrom:"", dateTo:"", selectedUF:"", panelTab:"summary", ufTab:"overview", ticketPage:1, ticketPageSize:25, ticketSearch:"", distributionDimension:"status", collapsed:true, day:"" };
  const initialParams=new URLSearchParams(location.search);
  [["region","region"],["layer","layer"],["category","category"],["mode","mode"],["client","client"],["project","project"],["provider","provider"],["period","period"],["reason","reason"],["status","status"],["priority","priority"],["arion_status","arionStatus"],["assignee","assignee"]].forEach(([param,key])=>{if(initialParams.has(param))state[key]=initialParams.get(param);});
  state.selectedUF=(initialParams.get("uf")||"").toUpperCase();
  state.region=state.region.toUpperCase();
  if(/^\d{4}-(0[1-9]|1[0-2])$/.test(state.period)){const [year,month]=state.period.split("-").map(Number);state.dateFrom=`${state.period}-01`;state.dateTo=new Date(Date.UTC(year,month,0)).toISOString().slice(0,10);}
  state.dateFrom=initialParams.get("date_from")||state.dateFrom;state.dateTo=initialParams.get("date_to")||state.dateTo;
  if(!["base","projects","calls","repeat","motives","climate"].includes(state.mode))state.mode="base";
  if(!state.client)state.client="all";
  const isPresentation=root.dataset.presentation==="true";
  if(isPresentation){state.mode=initialParams.get("mode")||"calls";state.client=root.dataset.fixedClient;state.project=initialParams.get("project")??root.dataset.fixedProject;state.period=initialParams.get("period")||root.dataset.fixedPeriod;state.layer=initialParams.get("layer")||"analysis";}
  ui.period.disabled = true;
  ui.reasonWrap.hidden = true;
  let map = null, geojson = null, data = null, mapLoaded = false, requestSequence = 0, searchIndex = -1, panelHideTimer = 0, panelSearchTimer = 0, moreFiltersTimer = 0, tooltipMoveFrame = 0;
  let requestController = null, detailController = null, detailSequence = 0, hoveredUF = "", appliedSelectedUF = "";
  let tooltipKey = "", tooltipCloseTimer = 0, tooltipPoint = null;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const norm = value => String(value || "").trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  const reasonOption = () => (data?.reason_options || []).find(option => option.value === state.reason);
  const reasonLabel = () => reasonOption()?.label || state.reason;
  function renderActiveFilters(){
    if(!ui.activeFilters)return;
    const chips=[];
    if(!isPresentation&&state.client!=="all")chips.push(["client","Cliente: "+state.client]);
    const incidents=["calls","repeat","motives","climate"].includes(state.mode);
    if(state.region)chips.push(["region","Região: "+state.region]);if(incidents&&state.category)chips.push(["category","Categoria: "+state.category]);
    if(state.project)chips.push(["project","Projeto: "+displayProject(state.project)]);
    if(state.selectedUF)chips.push(["uf","UF: "+state.selectedUF]);
    if(state.provider)chips.push(["provider","Provedor: "+(data?.providers||[]).find(row=>String(row.id)===state.provider)?.name]);
    if(incidents&&state.reason)chips.push(["reason","Motivo: "+reasonLabel()]);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)){
      if(!/^\d{4}-\d{2}$/.test(state.period)&&state.period!=="this_month")chips.push(["period","Per\u00edodo: "+(state.dateFrom&&state.dateTo?state.dateFrom+" a "+state.dateTo:state.period)]);
      if(incidents&&state.status)chips.push(["status","Status OTRS: "+(statusLabels[state.status]||state.status)]);
      if(incidents&&state.priority)chips.push(["priority","Prioridade: "+state.priority]);
      if(incidents&&state.arionStatus)chips.push(["arionStatus","Status ARION: "+((data?.arion_statuses||[]).find(row=>row.value===state.arionStatus)?.label||state.arionStatus)]);
      if(incidents&&state.assignee)chips.push(["assignee","Respons\u00e1vel: "+(state.assignee==="unassigned"?"Sem respons\u00e1vel":(data?.assignees||[]).find(row=>String(row.id)===state.assignee)?.name||"Selecionado")]);
    }
    $("[data-filter-count]").textContent=String(chips.length);
    ui.activeFilters.hidden=!chips.length;
    ui.activeFilters.innerHTML=chips.map(([key,label])=>"<button type=\"button\" class=\"map-filter-chip\" aria-label=\"Remover filtro: "+esc(label)+"\" data-clear-filter=\""+esc(key)+"\"><span>"+esc(label)+"</span><span aria-hidden=\"true\">&#215;</span></button>").join("")+(chips.length?"<button type=\"button\" class=\"map-clear-filters\" aria-label=\"Limpar todos os filtros aplicados\" data-clear-all-filters>Limpar filtros</button>":"");
  }
  function clearFilterValue(key){
    if(key==="client"&&!isPresentation)state.client="all";
    else if(key==="region"){state.region="";state.selectedUF="";}
    else if(key==="category")state.category="";
    else if(key==="project")state.project="";
    else if(key==="provider")state.provider="";
    else if(key==="uf"){clearSelection();return;}
    else if(key==="reason")state.reason="";
    else if(key==="period"){state.period="last_month";state.dateFrom="";state.dateTo="";}
    else if(key==="status")state.status="";
    else if(key==="priority")state.priority="";
    else if(key==="arionStatus")state.arionStatus="";
    else if(key==="assignee")state.assignee="";
    ui.project.value=state.project;ui.provider.value=state.provider;ui.reason.value=state.reason;
    ui.status.value=state.status;ui.priority.value=state.priority;ui.arionStatus.value=state.arionStatus;
    ui.assignee.value=state.assignee;ui.period.value=state.period;
    ui.dateFrom.value=state.dateFrom;ui.dateTo.value=state.dateTo;
    page.querySelectorAll("[data-client]").forEach(item=>{const selected=item.dataset.client===state.client;item.classList.toggle("is-selected",selected);item.setAttribute("aria-pressed",String(selected));});
    fetchData().catch(()=>showError("Não foi possível limpar o filtro selecionado."));
  }
  const theme = () => document.documentElement.dataset.theme === "light" ? "light" : "dark";
  function getProjectPalette(value){
    const key=norm(value).replace(/[^a-z0-9]+/g,"_").replace(/^_+|_+$/g,"");
    const configured=data?.project_colors?.[value];
    if(configured)return {light:[configured,configured,configured],dark:[configured,configured,configured]};
    if(projectPalettes[projectPaletteAliases[key]||key])return projectPalettes[projectPaletteAliases[key]||key];
    let hash=0;for(const ch of key)hash=(hash*31+ch.charCodeAt(0))>>>0;
    const hue=hash%360;
    return {light:[`hsl(${hue},30%,91%)`,`hsl(${hue},37%,64%)`,`hsl(${hue},44%,39%)`],dark:[`hsl(${hue},22%,21%)`,`hsl(${hue},32%,43%)`,`hsl(${hue},45%,70%)`]};
  }
  const dominantProject=row=>Object.entries(row?.project_counts||{}).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],"pt-BR"))[0];
  function closeTooltip(){
    clearTimeout(tooltipCloseTimer);cancelAnimationFrame(tooltipMoveFrame);tooltipMoveFrame=0;
    tooltipPoint=null;ui.hoverTooltip.hidden=true;ui.hoverTooltip.innerHTML="";tooltipKey="";
  }
  function deferCloseTooltip(){clearTimeout(tooltipCloseTimer);tooltipCloseTimer=setTimeout(()=>{if(!hoveredUF)closeTooltip();},90);}
  let rightSurface="", previousSurface="";
  function syncRightSurface(){
    const analytics=page.querySelector("[data-geo-intelligence]");
    const surface=ui.moreFilters.open?"filters":rightSurface;
    page.dataset.rightSurface=surface;
    const showAnalytics=surface==="analysis-summary"||surface==="analysis-expanded";
    if(analytics)analytics.hidden=!showAnalytics;
    panel.hidden=surface!=="details";
    panel.inert=surface!=="details";
    panel.setAttribute("aria-hidden",String(surface!=="details"));
    panel.classList.toggle("is-open",surface==="details");
    panelBackdrop.classList.remove("is-visible");
    page.classList.toggle("has-analytics-drawer",surface==="analysis-expanded");
    if(surface&&window.innerWidth<=1180&&!state.collapsed)setNationalCollapsed(true);
    updateMapLayout();
    page.dispatchEvent(new CustomEvent("geo:layout"));
  }
  function syncMoreFiltersOpen(){
    const open=ui.moreFilters.open,toolbar=page.querySelector(".geo-toolbar--map");
    toolbar.classList.toggle("is-more-filters-open",open);
    ui.moreFilters.querySelector("summary").setAttribute("aria-expanded",String(open));
    syncRightSurface();
    if(!open)clearTimeout(moreFiltersTimer);
  }

  function closeMoreFilters(returnFocus=false){
    if(!ui.moreFilters.open)return;
    ui.moreFilters.open=false;syncMoreFiltersOpen();
    if(returnFocus)ui.moreFilters.querySelector("summary").focus();
  }
  function closeSearch(clearValue=false){
    ui.results.hidden=true;ui.search.setAttribute("aria-expanded","false");ui.search.removeAttribute("aria-activedescendant");searchIndex=-1;
    if(clearValue)ui.search.value="";
    page.classList.remove("is-search-open");
  }
  const showError = (detail, title = "Mapa indisponível") => { message.hidden = true; errorBox.hidden = false; $("[data-map-error-title]").textContent = title; $("[data-map-error-detail]").textContent = detail; };
  const climateKeys={rain:"precipitation_mm",wind:"gust_max_kmh",wind_speed:"wind_max_kmh",temperature:"temperature_mean_c",temperature_min:"temperature_min_c",temperature_max:"temperature_max_c",direction:"wind_direction_deg",correlation:"coincident_days"};
  const dailyClimateKeys={rain:"precipitation_sum",wind:"wind_gusts_10m_max",wind_speed:"wind_speed_10m_max",temperature:"temperature_2m_mean",temperature_min:"temperature_2m_min",temperature_max:"temperature_2m_max",direction:"wind_direction_10m_dominant"};
  const climateValue=row=>{
    if(!state.day)return row?.climate?.[climateKeys[state.layer]];
    const day=row?.climate?.daily?.find(r=>r.date===state.day);
    if(state.layer==="correlation")return day?.precipitation_sum==null?null:Number(day.precipitation_sum>=1&&(row.technical_daily_counts?.[state.day]||0)>0);
    return day?.[dailyClimateKeys[state.layer]];
  };
  const metricValue = row => {
    if (!row) return 0;
    if(climateKeys[state.layer])return climateValue(row)??0;
    if(state.day&&["calls","motives","climate"].includes(state.mode))return row.geo_daily_counts?.[state.day]??row.daily_counts?.[state.day]??0;
    if(state.layer==="calls"||state.layer==="motives")return row.geo_ticket_count??row.ticket_count??0;
    if(state.layer==="client")return row.total_links||0;
    if (state.mode === "calls") return row.ticket_count || 0;
    if (state.mode === "repeat") return row.recurrence_links || 0;
    if (state.mode === "motives") return row.geo_ticket_count??row.ticket_count??0;
    return row.total_links || 0;
  };
  const selectedTotal = () => {
    if (!data) return 0;
    if(state.day&&["calls","motives","climate"].includes(state.mode))return data.geo_daily_counts?.[state.day]??data.daily_counts?.[state.day]??0;
    if (state.mode === "calls") return data.ticket_count || 0;
    if (state.mode === "repeat") return data.recurrence_link_count || 0;
    if (state.mode === "motives") return data.terminal_ticket_count || 0;
    return data.filtered_source_total || 0;
  };
  function urlFor(path, overrides = {}) {
    const url = new URL(path, window.location.origin);
    const params = { mode:state.mode==="climate"?"calls":state.mode, cohort:"created", include_intelligence:"1", region:state.region, category:state.category, uf:state.selectedUF, client:state.client, project:state.project, provider:state.provider, reason:state.reason, period:state.period, date_from:state.dateFrom, date_to:state.dateTo, status:state.status, priority:state.priority, arion_status:state.arionStatus, assignee:state.assignee, ...overrides };
    Object.entries(params).forEach(([key,value]) => { if (value) url.searchParams.set(key,value); });
    if(isPresentation)url.searchParams.set("project",state.project);
    return url;
  }
  function syncUrl(){
    const url=new URL(location.href);
    ["region","layer","category","mode","client","project","period","uf","reason","provider","date_from","date_to","status","priority","arion_status","assignee","provenance"].forEach(key=>url.searchParams.delete(key));
    url.searchParams.set("mode",state.mode);
    if(state.region)url.searchParams.set("region",state.region);
    if(state.layer!=="analysis")url.searchParams.set("layer",state.layer);
    if(state.category)url.searchParams.set("category",state.category);
    if(state.client!=="all")url.searchParams.set("client",state.client);
    if(state.project)url.searchParams.set("project",state.project);
    if(state.provider)url.searchParams.set("provider",state.provider);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)){
      url.searchParams.set("period",state.period);
      if(state.dateFrom)url.searchParams.set("date_from",state.dateFrom);
      if(state.dateTo)url.searchParams.set("date_to",state.dateTo);
    }
    if(state.reason)url.searchParams.set("reason",state.reason);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)&&state.status)url.searchParams.set("status",state.status);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)&&state.priority)url.searchParams.set("priority",state.priority);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)&&state.arionStatus)url.searchParams.set("arion_status",state.arionStatus);
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)&&state.assignee)url.searchParams.set("assignee",state.assignee);
    if(state.selectedUF)url.searchParams.set("uf",state.selectedUF);
    history.replaceState(null,"",url.pathname+url.search+url.hash);
  }
  function updateModeControls(){
    geoController.syncMode();
    const temporal=["projects","calls","repeat","motives","climate"].includes(state.mode);
    ui.period.disabled=false;
    ui.periodControl.hidden=false;
    ui.periodControl.classList.add("is-visible");
    ui.dates.hidden=!(temporal&&state.period==="custom");
    const incidents=["calls","repeat","motives","climate"].includes(state.mode);
    ui.reasonWrap.hidden=!incidents;
    const category=page.querySelector("[data-category-filter]");if(category){category.disabled=!incidents;category.title=incidents?"":"Suspenso: inventário CMDB não utiliza motivos OTRS.";}
    ui.ticketFilters.hidden=!incidents;
    ui.status.value=temporal?state.status:"";
    ui.priority.value=temporal?state.priority:"";
    ui.arionStatus.value=temporal?state.arionStatus:"";
    ui.assignee.value=temporal?state.assignee:"";
    page.querySelectorAll("[data-mode]").forEach(item=>{const selected=item.dataset.mode===state.mode;item.classList.toggle("is-selected",selected);item.setAttribute("aria-pressed",String(selected));});
    if(["this_month","last_month","last_30","last_60","last_90","custom"].includes(state.period))ui.period.value=state.period;
    else if(/^\d{4}-\d{2}$/.test(state.period)){
      let option=ui.period.querySelector('[data-month-option]');
      if(!option){option=document.createElement("option");option.dataset.monthOption="true";ui.period.append(option);}
      option.value=state.period;option.textContent=state.period;ui.period.value=state.period;
    }else ui.period.value="custom";
    ui.dateFrom.value=state.dateFrom;ui.dateTo.value=state.dateTo;
    renderActiveFilters();

    const titlePeriod=state.period.match(/^\d{4}-(0[1-9]|1[0-2])$/)?periodLabel({date_from:`${state.period}-01`}):periodLabel({date_from:state.dateFrom||data?.period?.date_from});
    const heading=state.mode==="base"?"Base de links":state.mode==="projects"?((state.project?"Projeto \u00b7 "+displayProject(state.project):"Projetos")+" \u00b7 "+titlePeriod):state.mode==="motives"?((state.reason?reasonLabel():"Motivos")+" \u00b7 "+titlePeriod):["calls","repeat"].includes(state.mode)?((state.mode==="calls"?"Chamados":"Reincid\u00eancia")+" \u00b7 "+titlePeriod):"Base de links";
    $("#geo-title").textContent=heading;
  }
  async function fetchData() {
    state.ticketPage=1;
    closeTooltip();
    const seq = ++requestSequence;
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    $(`[data-message-title]`).textContent = "Atualizando análise";
    $(`[data-message-description]`).textContent = "Aplicando filtros ao mapa atual.";
    message.hidden = false; errorBox.hidden = true;
    syncUrl();
    try {
      const response = await fetch(urlFor(root.dataset.apiUrl), { headers:{"X-Requested-With":"XMLHttpRequest"}, signal:controller.signal });
      if (!response.ok) {
        const error=await response.json().catch(()=>({})),message=error.error||error.message||`A consulta do Mapa respondeu HTTP ${response.status}.`;
        if(response.status===400&&typeof message==="string"&&/^(Projeto|ISP) incompatível/.test(message)&&seq===requestSequence){
          if(message.startsWith("Projeto"))state.project="";state.provider="";
          $("[data-filter-notice]").textContent=message+" A seleção incompatível foi removida.";
          return await fetchData();
        }
        throw new Error(message);
      }
      const payload = await response.json();
      if (!payload.states || !Array.isArray(payload.projects) || !Array.isArray(payload.providers)) throw new Error("A API retornou um formato inesperado.");
      if (seq !== requestSequence) return false;
      data = payload;
      if(!state.selectedUF){closePanel();ui.breadcrumb.hidden=true;updateMapLayout();}
      fillFilters();
      updateModeControls();syncUrl();
      renderMap(); renderLegend(); renderNational();
      if(lastFitScope!==`${state.region}|${state.selectedUF}`){manualCamera=false;refitCurrentMap();lastFitScope=`${state.region}|${state.selectedUF}`;}
      geoController.update(payload.geo_intelligence);
      message.hidden = true;
      if (state.selectedUF) await loadState(state.selectedUF);
      return true;
    } catch (error) {
      if (seq !== requestSequence || (controller.signal.aborted && !timedOut)) return false;
      if (timedOut) throw new Error("A consulta do Mapa excedeu o tempo limite.");
      throw error;
    } finally {
      clearTimeout(timeout);
      if (requestController === controller) requestController = null;
    }
  }
  function displayProject(value){
    const known={"BANCO DO BRASIL":"BANCO DO BRASIL","CAIXA ECONOMICA FEDERAL":"Caixa Econ\u00f4mica Federal","CORREIOS":"Correios","BRADESCO":"BRADESCO","ARCOS DOURADOS":"Arcos Dourados","DIVERSOS":"Diversos"};
    return known[norm(value).toLocaleUpperCase("pt-BR")]||String(value||"").toLocaleLowerCase("pt-BR").replace(/(^|\s)\S/g,letter=>letter.toLocaleUpperCase("pt-BR"));
  }
  function periodLabel(period){
    const value=period?.date_from||"";
    if(!value)return "per\u00edodo";
    return new Intl.DateTimeFormat("pt-BR",{month:"long",year:"numeric",timeZone:"UTC"}).format(new Date(`${value}T12:00:00Z`));
  }
  function fillFilters() {
    ui.project.innerHTML = '<option value="">Todos os projetos</option>' + data.projects.map(value=>`<option value="${esc(value)}">${esc(displayProject(value))}</option>`).join("");
    const selectedProject = data.selected_project || "";
    state.project = data.projects.includes(selectedProject) ? selectedProject : "";
    ui.project.value = state.project;
    ui.provider.innerHTML = '<option value="">Todos os provedores</option>' + data.providers.map(row=>`<option value="${esc(row.id)}">${esc(row.name)}</option>`).join("");
    ui.priority.innerHTML='<option value="">Todas</option>'+(data.priority_options||[]).map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join("");
    ui.arionStatus.innerHTML='<option value="">Todos</option>'+(data.arion_statuses||[]).map(row=>`<option value="${esc(row.value)}">${esc(row.label)}</option>`).join("");
    ui.assignee.innerHTML='<option value="">Todos</option><option value="unassigned">Sem responsável</option>'+(data.assignees||[]).map(row=>`<option value="${esc(row.id)}">${esc(row.name)}</option>`).join("");
    state.provider = data.providers.some(row=>String(row.id)===state.provider) ? state.provider : "";ui.provider.value=state.provider;
    ui.arionStatus.value=(data.arion_statuses||[]).some(row=>row.value===state.arionStatus)?state.arionStatus:"";
    ui.assignee.value=state.assignee==="unassigned"||((data.assignees||[]).some(row=>String(row.id)===state.assignee))?state.assignee:"";
    const oldReason = state.reason;
    const reasonOptions = data.reason_options || (data.reasons || []).map(label => ({value:label,label,raw_values:[label]}));
    ui.reason.innerHTML = '<option value="">Todos os motivos</option>' + reasonOptions.map(option=>`<option value="${esc(option.value)}">${esc(option.label)}</option>`).join("");
    const selectedReason = reasonOptions.find(option => option.value === oldReason || option.label === oldReason || (option.raw_values || []).includes(oldReason));
    if(!["base","projects"].includes(state.mode))state.reason=selectedReason?.value||"";
    ui.reason.value=selectedReason?.value||"";
  }
  function dominantClient(row) {
    const counts=row?.client_counts || {};
    const unidentified=counts["Cliente não identificado"]||0;
    const identified={claro:0,vivo:0,algar:0,other:0};
    Object.entries(counts).forEach(([name,value])=>{
      const key=norm(name);
      if(key==="cliente nao identificado") return;
      if(key==="claro"||key==="vivo"||key==="algar") identified[key]+=value;
      else identified.other+=value;
    });
    const winner=Object.entries(identified).sort((a,b)=>b[1]-a[1])[0];
    return !winner||unidentified>=winner[1]?"unclassified":winner[0];
  }
  function dominantClientLabel(row) {
    const dominant=dominantClient(row);
    return dominant==="unclassified"?"Cliente n\u00e3o identificado":dominant==="other"?"Outras":dominant.charAt(0).toUpperCase()+dominant.slice(1);
  }
  function maxMetric() { return Math.max(1,...Object.values(data?.states || {}).map(metricValue)); }
  function featureCollection() {
    const maximum=maxMetric();
    return { type:"FeatureCollection", features:geojson.features.filter(feature=>!state.region||(data?.allowed_ufs||[]).includes(feature.properties.sigla)).map(feature=>{
      const code=feature.properties.sigla,row=data.states[code] || {};
      return { ...feature, id:code, properties:{...feature.properties,value:metricValue(row),dominant:dominantClient(row),project_color:getProjectPalette(dominantProject(row)?.[0]||"")[theme()][2],cause:Object.entries(row.geo_category_counts||row.category_counts||{}).sort((a,b)=>b[1]-a[1])[0]?.[0]||"",intensity:state.layer==="direction"?.45:climateKeys[state.layer]?Math.max(0,Math.min(1,state.layer.startsWith("temperature")?(metricValue(row)+15)/60:metricValue(row)/maximum)):Math.sqrt(metricValue(row)/maximum),has_data:climateKeys[state.layer]?Number.isFinite(climateValue(row)):Boolean(row.total_links||metricValue(row)>0),context_faded:Boolean(state.selectedUF&&state.selectedUF!==code)} };
    })};
  }
  function labelCollection() {
    return {type:"FeatureCollection",features:Object.entries(stateLabelCoordinates).filter(([code])=>!state.region||(data?.allowed_ufs||[]).includes(code)).map(([code,coordinates])=>({
      type:"Feature",geometry:{type:"Point",coordinates},properties:{sigla:code,
        weather_label:climateKeys[state.layer]&&Number.isFinite(climateValue(data?.states?.[code]))?number.format(climateValue(data.states[code]))+(state.layer.startsWith("temperature")?"°":state.layer==="rain"?" mm":["wind","wind_speed"].includes(state.layer)?" km/h":""):"",
        wind_direction:state.layer==="direction"?climateValue(data?.states?.[code])??0:0,
        directional:state.layer==="direction"&&Number.isFinite(climateValue(data?.states?.[code]))},
    }))};
  }
  function calloutCollection() {
    const features=[];
    Object.entries(smallStateCallouts).forEach(([code,positions])=>{
      if(state.region&&!(data?.allowed_ufs||[]).includes(code))return;
      features.push({type:"Feature",id:code,geometry:{type:"LineString",coordinates:[positions.anchor,positions.label]},properties:{sigla:code}});
      features.push({type:"Feature",id:code,geometry:{type:"Point",coordinates:positions.label},properties:{sigla:code}});
    });
    return {type:"FeatureCollection",features};
  }
  const colorRamp = colors => ["interpolate",["linear"],["get","intensity"],0,colors[0],.45,colors[1],1,colors[2]];
  function baseColorExpression() {
    const colors=brandPalettes[theme()];
    if (state.client !== "all") {
      const key=state.client==="other"?"other":norm(state.client);
      return colorRamp(colors[key] || colors.other);
    }
    return ["match",["get","dominant"],"claro",colorRamp(colors.claro),"vivo",colorRamp(colors.vivo),"algar",colorRamp(colors.algar),"other",colorRamp(colors.other),colors.unidentified];
  }
  const geoRamps={rain:{light:["#e3edf6","#85acd0","#315f98"],dark:["#25384b","#5888ba","#b0d6fa"]},wind:{light:["#eee9f7","#ad91c6","#67508f"],dark:["#352945","#9173b2","#d0b3ef"]},correlation:{light:["#f8eadf","#d4a07d","#a35e3f"],dark:["#45332a","#ba8b66","#f0bd91"]}};
  geoRamps.temperature={light:["#d4e4ef","#dfb78c","#b56652"],dark:["#304956","#b9916b","#de937f"]};
  ["temperature_min","temperature_max"].forEach(k=>geoRamps[k]=geoRamps.temperature);geoRamps.wind_speed=geoRamps.wind;geoRamps.direction=geoRamps.wind;
  function causeMatchExpression(modeName){
    return ["match",["get","cause"],...CAUSE_ORDER.flatMap(label=>[label,causeColor(label,modeName)]),modeName==="light"?"#787f85":"#7a8288"];
  }
  function fillExpression() {
    const modeName=theme();
    const emptyColor=modeName==="light"?"#d5d8dc":"#363839";
    if(state.mode==="projects"&&!state.project&&state.layer==="analysis")return ["case",["get","has_data"],["get","project_color"],emptyColor];
    if(state.layer==="motives")return ["case",["get","has_data"],causeMatchExpression(modeName),emptyColor];
    if(geoRamps[state.layer])return ["case",["get","has_data"],colorRamp(geoRamps[state.layer][modeName]),emptyColor];
    if(state.layer==="client")return ["case",["get","has_data"],state.project?colorRamp(getProjectPalette(state.project)[modeName]):baseColorExpression(),emptyColor];
    if(state.layer==="calls")return ["case",["get","has_data"],colorRamp(quantitative.calls[modeName]),emptyColor];
    const colors=(state.mode==="projects"?getProjectPalette(state.project):quantitative[state.mode]||quantitative.calls)[modeName];
    const metricColors=state.mode==="base"?baseColorExpression():colorRamp(colors);
    return ["case",["boolean",["get","has_data"],false],metricColors,modeName==="light"?"#d5dcda":"#363839"];
  }
  function setFeatureState(code, key, value) {
    if (!code || !map) return;
    ["arion-states","arion-state-callouts"].forEach(source=>{
      if (!map.getSource(source)) return;
      try { map.setFeatureState({source,id:code},{[key]:value}); } catch (_error) { /* Source may be refreshing. */ }
    });
  }
  function syncFeatureStates() {
    if (appliedSelectedUF && appliedSelectedUF !== state.selectedUF) setFeatureState(appliedSelectedUF,"selected",false);
    if (state.selectedUF) setFeatureState(state.selectedUF,"selected",true);
    appliedSelectedUF=state.selectedUF;
    if (hoveredUF) setFeatureState(hoveredUF,"hover",true);
  }
  function renderMap() {
    if (!map || !geojson) return;
    if (map.getSource("arion-states")) map.getSource("arion-states").setData(featureCollection());
    if(map.getSource("arion-state-label-points"))map.getSource("arion-state-label-points").setData(labelCollection());
    if(map.getSource("arion-state-callouts"))map.getSource("arion-state-callouts").setData(calloutCollection());
    syncFeatureStates();
    closeTooltip();
    const animateWeather=page.querySelector("[data-weather-animate]")?.checked!==false&&!window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (map.getLayer("arion-state-fill")) {map.setPaintProperty("arion-state-fill","fill-color-transition",{duration:animateWeather?420:0,delay:0});map.setPaintProperty("arion-state-fill","fill-color",fillExpression());}
    if (map.getLayer("arion-state-fill")) map.setPaintProperty("arion-state-fill","fill-opacity",["case",["boolean",["feature-state","selected"],false],.98,["boolean",["get","has_data"],false],["case",["get","context_faded"],.64,.92],["case",["get","context_faded"],.32,.48]]);
    if (map.getLayer("arion-state-outline")) {
      map.setPaintProperty("arion-state-outline","line-color",["case",["boolean",["feature-state","selected"],false],theme()==="light"?"#496454":"#e3eee5",["boolean",["feature-state","hover"],false],theme()==="light"?"#718579":"#d1ded4",theme()==="light"?"rgba(48,67,56,.36)":"rgba(221,233,224,.34)"]);
      map.setPaintProperty("arion-state-outline","line-width",["case",["boolean",["feature-state","selected"],false],2.2,["boolean",["feature-state","hover"],false],1.6,.8]);
    }
    if (map.getLayer("arion-state-selected-halo")) {
      map.setPaintProperty("arion-state-selected-halo","line-color",theme()==="light"?"#789382":"#c3d8c8");
      map.setPaintProperty("arion-state-selected-halo","line-opacity",["case",["boolean",["feature-state","selected"],false],.2,0]);
    }
    if (map.getLayer("arion-state-labels")) {
      map.setPaintProperty("arion-state-labels","text-color",theme()==="light"?"#25372f":"#e4ebe5");
      map.setPaintProperty("arion-state-labels","text-halo-color",theme()==="light"?"rgba(235,240,235,.94)":"rgba(17,22,20,.94)");
      map.setPaintProperty("arion-state-labels","text-opacity",state.selectedUF?["case",["==",["get","sigla"],state.selectedUF],1,.74]:.9);
    }
    if(map.getLayer("arion-weather-labels")){
      map.setPaintProperty("arion-weather-labels","text-color",theme()==="light"?"#25372f":"#e4ebe5");
      map.setPaintProperty("arion-weather-labels","text-halo-color",theme()==="light"?"#e5eae6":"#151b18");
    }
    if (map.getLayer("arion-state-callout-lines")) {
      map.setPaintProperty("arion-state-callout-lines","line-color",["case",["boolean",["feature-state","selected"],false],theme()==="light"?"#496454":"#d9e7dc",["boolean",["feature-state","hover"],false],theme()==="light"?"#718579":"#c7d6cb",theme()==="light"?"rgba(48,67,56,.5)":"rgba(221,233,224,.45)"]);
      map.setPaintProperty("arion-state-callout-lines","line-width",["case",["boolean",["feature-state","selected"],false],1.5,["boolean",["feature-state","hover"],false],1.4,1]);
    }
    if (map.getLayer("arion-state-callout-labels")) {
      map.setPaintProperty("arion-state-callout-labels","text-color",theme()==="light"?"#31463a":"#e5ece6");
      map.setPaintProperty("arion-state-callout-labels","text-halo-color",theme()==="light"?"#f2f5f1":"#151b18");
      map.setPaintProperty("arion-state-callout-labels","text-opacity",state.selectedUF?["case",["==",["get","sigla"],state.selectedUF],1,.82]:1);
    }
    $("[data-map-context]")?.replaceChildren();
  }
  function renderLegend() {
    if(state.mode==="projects"&&!state.project&&state.layer==="analysis"){
      const names=[...new Set(Object.values(data?.states||{}).map(row=>dominantProject(row)?.[0]).filter(Boolean))].sort();
      ui.legend.innerHTML='<span class="legend-scale-label">Projeto predominante · links distintos</span>'+names.map(name=>`<span class="legend-item"><i class="legend-dot" style="background:${getProjectPalette(name)[theme()][2]}"></i>${esc(displayProject(name))}</span>`).join("");return;
    }
    if(state.layer==="direction"){
      ui.legend.innerHTML='<span class="legend-scale-label">Setas: direção aproximada de origem · graus · Cinza: sem dados</span>';
      return;
    }
    if(state.layer==="client"){
      const colors=brandPalettes[theme()];
      if(!state.project&&state.client==="all"){
        ui.legend.innerHTML=[...Object.entries({claro:"Claro",vivo:"Vivo",algar:"Algar",other:"Outras"})].map(([key,label])=>`<span class="legend-item"><i class="legend-dot" style="background:${colors[key][2]}"></i>${label}</span>`).join("");
      }else{
        const palette=state.project?getProjectPalette(state.project)[theme()]:(colors[norm(state.client)]||colors.other);
        ui.legend.innerHTML=`<span class="legend-scale-label">Links CMDB \u00b7 ${esc(state.project||state.client)}</span><span class="legend-scale" style="background:linear-gradient(90deg,${palette.join(",")})"></span><span class="legend-scale-label">0 \u2014 ${number.format(maxMetric())}</span>`;
      }
      return;
    }
    if(state.layer==="motives"){
      const modeName=theme();
      ui.legend.innerHTML=CAUSE_ORDER.filter(label=>!isPresentation||((data?.category_counts?.[label]||0)>0&&label!=="Pendência Financeira")).map(label=>`<span class="legend-item"><i class="legend-dot" style="background:${causeColor(label,modeName)}"></i><kbd class="geo-legend-code">${causeBadge(label)}</kbd>${esc(label)}</span>`).join("");
      return;
    }
    if(geoRamps[state.layer]||state.layer==="calls"){
      const ramps=geoRamps[state.layer]||quantitative.calls,colors=ramps[theme()],labels={temperature:"Temperatura média · °C",temperature_min:"Temperatura mínima · °C",temperature_max:"Temperatura máxima · °C",wind_speed:"Vento máximo · km/h",direction:"Direção dominante · graus",rain:"Chuva estimada · mm",wind:"Rajada máxima · km/h",correlation:"Dias com chuva ≥ 1 mm e incidentes técnicos · sem causalidade",calls:"Incidentes válidos"};
      if(geoRamps[state.layer]&&!Object.values(data?.states||{}).some(row=>Number.isFinite(climateValue(row)))){
        ui.legend.innerHTML=`<span class="legend-scale-label">${esc(labels[state.layer])} · Sem dados neste recorte · Cinza: ausência de observação</span>`;return;
      }
      ui.legend.innerHTML=`<span class="legend-scale-label">${esc(labels[state.layer])}</span><span class="legend-scale" style="background:linear-gradient(90deg,${colors.join(",")})"></span><span class="legend-scale-label">${state.layer.startsWith("temperature")?"−15":"0"} — ${state.layer.startsWith("temperature")?"45":number.format(maxMetric())} · ${state.day?esc(state.day):"Mês"} · Cinza: sem dados</span>`;return;
    }
    if (state.mode === "base" && isPresentation) {
      ui.legend.innerHTML=`<span class="legend-item"><i class="legend-dot" style="background:${brandPalettes[theme()][norm(state.client)]?.[2]||"#768ba5"}"></i>${esc(state.client)} · links CMDB</span>`;
    } else if (state.mode === "base") {
      ui.legend.classList.remove("geo-legend--scale");
      const colors=brandPalettes[theme()];
      ui.legend.innerHTML=`<span class="legend-item"><i class="legend-dot" style="background:${colors.claro[2]}"></i>Claro</span><span class="legend-item"><i class="legend-dot" style="background:${colors.vivo[2]}"></i>Vivo</span><span class="legend-item"><i class="legend-dot" style="background:${colors.algar[2]}"></i>Algar</span><span class="legend-item"><i class="legend-dot" style="background:${colors.other[2]}"></i>Outras</span><span class="legend-item"><i class="legend-dot" style="background:${colors.unidentified}"></i>N\u00e3o identificado</span>`;
    } else {
      ui.legend.classList.add("geo-legend--scale");
      const label=state.mode==="projects"&&state.project?displayProject(state.project):state.mode==="motives"&&state.reason?reasonLabel():modeLabels[state.mode];
      const colors=(state.mode==="projects"?getProjectPalette(state.project):quantitative[state.mode]||quantitative.calls)[theme()];
      ui.legend.innerHTML=`<span class="legend-scale-label">Menor ${esc(label)}</span><span class="legend-scale legend-scale--${state.mode}" style="background:linear-gradient(90deg,${colors.join(",")})"></span><span class="legend-scale-label">Maior</span>`;
    }
    const source={base:"CMDB real \u00b7 base atual",projects:"CMDB real \u00b7 projetos atuais",calls:"OTRS + CMDB \u00b7 chamados",repeat:"OTRS + CMDB \u00b7 reincid\u00eancia",motives:"OTRS \u00b7 motivos de encerramento"};
    ui.source.innerHTML=`<i></i>${source[state.mode]}`;
  }
  const macroRegions={N:["AC","AM","AP","PA","RO","RR","TO"],NE:["AL","BA","CE","MA","PB","PE","PI","RN","SE"],CO:["DF","GO","MS","MT"],SE:["ES","MG","RJ","SP"],S:["PR","RS","SC"]};
  const macroNames={N:"Norte",NE:"Nordeste",CO:"Centro-Oeste",SE:"Sudeste",S:"Sul"};
  function renderNational() {
    if (!data || !ui.nationalView) return;
    national.querySelector(".section-kicker").textContent=state.selectedUF||state.region||"BRASIL";
    national.querySelector(".national-subtitle").textContent=state.selectedUF?stateNames[state.selectedUF]:state.region?(macroNames[state.region]||"Visão regional"):"Panorama territorial";
    const rows=Object.entries(data.states).map(([code,row])=>({code,name:stateNames[code]||code,row,value:metricValue(row)})).sort((a,b)=>b.value-a.value||a.code.localeCompare(b.code));
    const allowedUFs=new Set(data.allowed_ufs||Object.keys(stateNames));
    const scopedRows=rows.filter(r=>allowedUFs.has(r.code));
    const activeRows=scopedRows.filter(r=>r.value>0);
    const totalSum=activeRows.reduce((acc,r)=>acc+r.value,0);
    const unlinkedCount=Number(data.unlinked_ticket_count||0);
    const unlinkedNote=unlinkedCount?`<p class="panel-empty">${number.format(unlinkedCount)} tickets sem Link CMDB local somados ao total nacional (sem UF atribuída).</p>`:"";

    if(isPresentation){
      const presRows=rows.filter(row=>(row.row.ticket_count||0)>0).sort((a,b)=>b.row.ticket_count-a.row.ticket_count);
      ui.nationalView.innerHTML=`<div class="geo-executive-summary"><h3>${esc(state.selectedUF||state.region||"Brasil")} · ${esc(state.client)}</h3><div class="geo-executive-metrics"><div><strong>${number.format(data.filtered_source_total||0)}</strong><span>Links CMDB</span></div><div><strong>${number.format(data.ticket_count||0)}</strong><span>Incidentes válidos</span></div></div><div class="geo-reasons"><h3>Links por macrorregião</h3>${Object.entries(data.region_link_counts||{}).map(([name,count])=>`<p><span>${esc(name)}</span><b>${number.format(count)}</b></p>`).join("")}</div><h3>Principais UFs em incidentes</h3>${presRows.slice(0,5).map(row=>`<button type="button" class="geo-executive-uf" data-ranking-code="${row.code}"><span>${esc(row.name)}</span><strong>${number.format(row.row.ticket_count)}</strong></button>`).join("")||'<p class="panel-empty">Sem incidentes por UF.</p>'}</div>`;
      return;
    }

    if (state.panelTab==="summary") {
      const top3Sum=activeRows.slice(0,3).reduce((acc,r)=>acc+r.value,0);
      const top3Pct=totalSum>0?(top3Sum/totalSum*100).toLocaleString("pt-BR",{maximumFractionDigits:1}):"0";
      const leader=activeRows[0];
      const regTotals=Object.entries(macroRegions).map(([sigla,ufs])=>{
        const subset=scopedRows.filter(r=>ufs.includes(r.code)&&Number.isFinite(r.value));
        const val=state.mode==="climate"&&state.layer.startsWith("temperature")
          ? (subset.length?subset.reduce((a,r)=>a+r.value,0)/subset.length:0)
          : subset.reduce((a,r)=>a+r.value,0);
        return {sigla,name:macroNames[sigla],value:val};
      });
      const regSum=regTotals.reduce((a,r)=>a+Math.max(0,r.value),0)||1;
      ui.nationalView.innerHTML=`<div class="national-concentration-grid"><div class="national-stat-tile"><strong>${activeRows.length} / ${scopedRows.length}</strong><span>UFs com registros</span></div><div class="national-stat-tile"><strong>${top3Pct}%</strong><span>Concentração Top 3</span></div></div>`
        +`<div class="national-leaders"><div><span>Líder territorial</span><strong>${esc(leader?.name||"Sem dados")}</strong><small>${leader?number.format(leader.value)+" "+esc(modeLabels[state.mode]||""):"—"}</small></div><div><span>Recorte ativo</span><strong>${esc(state.region?macroNames[state.region]:"Nacional")}</strong><small>${["base","projects"].includes(state.mode)?"Inventário CMDB":esc(periodLabel(data.period))}</small></div></div>`
        +`<div class="national-region-list">${regTotals.map(r=>{const pct=Math.max(0,(r.value/regSum)*100);const active=state.region===r.sigla?" is-active":"";const displayVal=state.mode==="climate"?r.value.toLocaleString("pt-BR",{maximumFractionDigits:1}):number.format(Math.round(r.value));return `<button type="button" class="national-region-btn${active}" data-national-region="${r.sigla}" title="Filtrar macrorregião ${esc(r.name)}"><span>${esc(r.name)} (${r.sigla})</span><b>${displayVal}${state.mode==="climate"&&state.layer.startsWith("temperature")?"°":" · "+pct.toLocaleString("pt-BR",{maximumFractionDigits:0})+"%"}</b><span class="national-region-bar"><i style="width:${Math.min(100,pct)}%"></i></span></button>`;}).join("")}</div>`
        +unlinkedNote;
    } else if (state.panelTab==="ranking") {
      const rankSum=Math.max(1,totalSum);
      ui.nationalView.innerHTML=`<div class="national-view-heading"><span>Ranking territorial · ${esc(modeLabels[state.mode]||state.mode)}</span><span class="demo-inline">${activeRows.length} UFs</span></div><ol class="national-ranking">${activeRows.slice(0,27).map((row,i)=>`<li><button type="button" class="national-rank-button" data-ranking-code="${row.code}" aria-label="Abrir ${esc(row.name)}, ${number.format(row.value)}"><span class="national-rank-number">${String(i+1).padStart(2,"0")}</span><img src="${esc(flags[row.code]||"")}" alt="" width="22" height="15" loading="lazy"><span class="national-rank-name">${esc(row.name)} <small>(${(row.value/rankSum*100).toLocaleString("pt-BR",{maximumFractionDigits:1})}%)</small></span><strong>${number.format(row.value)}</strong></button></li>`).join("")||"<li>Nenhum dado para este recorte.</li>"}</ol>${unlinkedNote}`;
    } else {
      let distribution={},distributionLabel="",dimensionSelector="";
      if(state.mode==="base") { distributionLabel="Distribuição por cliente";Object.values(data.states).forEach(row=>Object.entries(row.client_counts||{}).forEach(([key,value])=>{const normKey=norm(key);const bucket=normKey==="claro"?"Claro":normKey==="vivo"?"Vivo":normKey==="algar"?"Algar":normKey==="cliente nao identificado"?"Cliente não identificado":"Outras";distribution[bucket]=(distribution[bucket]||0)+value;})); }
      else if(state.mode==="projects") { distributionLabel="Distribuição por provedor";Object.values(data.states).forEach(row=>Object.entries(row.provider_counts||{}).forEach(([key,value])=>distribution[key]=(distribution[key]||0)+value)); }
      else if(state.mode==="calls") {
        const dimensions={client:["Cliente",data.ticket_client_counts||{}],project:["Projeto",data.ticket_project_counts||{}],provider:["Provedor",data.ticket_provider_counts||{}],status:["Status",data.ticket_status_counts||{}],reason:["Motivo",data.ticket_reason_counts||{}]};
        const selected=dimensions[state.distributionDimension]||dimensions.client;distributionLabel=`Concentração por ${selected[0].toLocaleLowerCase("pt-BR")}`;distribution=selected[1];
        dimensionSelector=`<label class="distribution-dimension"><span>Eixo</span><select data-distribution-filter>${Object.entries(dimensions).map(([key,value])=>`<option value="${key}" ${key===state.distributionDimension?"selected":""}>${value[0]}</option>`).join("")}</select></label>`;
      }
      else if(state.mode==="repeat") { distributionLabel="Reincidentes por cliente";distribution=data.recurrence_client_counts||{}; }
      else if(state.mode==="climate") { distributionLabel="UFs no recorte climático";activeRows.slice(0,8).forEach(r=>{distribution[r.name]=r.value;}); }
      else { distributionLabel="Distribuição por motivo";Object.values(data.states).forEach(row=>Object.entries(row.reason_counts||{}).forEach(([key,value])=>distribution[key]=(distribution[key]||0)+value)); }
      const allItems=Object.entries(distribution).filter(([,value])=>value>0).sort((a,b)=>b[1]-a[1]),sum=allItems.reduce((a,item)=>a+item[1],0),items=allItems.slice(0,8);
      ui.nationalView.innerHTML=`<div class="national-view-heading"><span>${esc(distributionLabel)}</span>${dimensionSelector}<span class="demo-inline">${number.format(sum)}</span></div><div class="national-distribution">${items.map(([label,value])=>{const key=norm(label);const tone=key==="claro"?"claro":key==="vivo"?"vivo":key==="algar"?"algar":key==="cliente nao identificado"?"unidentified":"other";return `<div class="national-distribution-row"><span><i class="operator-swatch operator-swatch--${tone}"></i>${esc(label)}</span><strong>${sum?(value/sum*100).toLocaleString("pt-BR",{maximumFractionDigits:1}):0}%</strong><small>${number.format(value)}</small><i class="national-distribution-track"><b class="operator-fill--${tone}" style="width:${Math.max(0,value/sum*100)}%"></b></i></div>`;}).join("")||"<p>Nenhum dado para este recorte.</p>"}</div>${unlinkedNote}`;
    }
  }
  ui.nationalView.addEventListener("change",event=>{if(event.target.matches("[data-distribution-filter]")){state.distributionDimension=event.target.value;renderNational();}});
  ui.nationalView.addEventListener("click",event=>{
    const regBtn=event.target.closest("[data-national-region]");
    if(regBtn){
      const nextReg=regBtn.dataset.nationalRegion||"";
      state.region=state.region===nextReg?"":nextReg;
      if(state.selectedUF&&state.region&&!macroRegions[state.region].includes(state.selectedUF))state.selectedUF="";
      updateModeControls();fetchData().catch(()=>showError("Não foi possível aplicar a macrorregião."));
      return;
    }
    const row=event.target.closest("[data-ranking-code]");
    if(row)selectState(row.dataset.rankingCode);
  });
  function setPanelTab(tab) {
    state.panelTab=tab;
    $("[data-national-tab='summary']").setAttribute("aria-selected",String(tab==="summary"));
    $("[data-national-tab='ranking']").setAttribute("aria-selected",String(tab==="ranking"));
    $("[data-national-tab='distribution']").setAttribute("aria-selected",String(tab==="distribution"));
    renderNational();
  }
  function listRows(target, values, suffix="links") {
    const rows=Object.entries(values||{}).filter(([,count])=>count).sort((a,b)=>b[1]-a[1]).slice(0,6);
    target.innerHTML=rows.map(([label,count])=>`<div class="panel-reason-row"><span>${esc(label)}</span><b>${number.format(count)} ${suffix}</b></div>`).join("")||'<p class="panel-empty">Sem registros neste recorte.</p>';
  }
  function listDimensionRows(target, linkValues, ticketValues, normalize=value=>value) {
    const links={},tickets={};
    Object.entries(linkValues||{}).forEach(([name,count])=>{const label=normalize(name);links[label]=(links[label]||0)+count;});
    Object.entries(ticketValues||{}).forEach(([name,count])=>{const label=normalize(name);tickets[label]=(tickets[label]||0)+count;});
    const names=[...new Set([...Object.keys(links),...Object.keys(tickets)])]
      .sort((a,b)=>(links[b]||0)-(links[a]||0)||a.localeCompare(b,"pt-BR"))
      .slice(0,8);
    target.innerHTML=names.map(name=>`<div class="panel-reason-row"><span>${esc(name)}</span><b>${number.format(links[name]||0)} links · ${number.format(tickets[name]||0)} tickets</b></div>`).join("")||'<p class="panel-empty">Sem registros neste recorte.</p>';
  }
  const statusLabels={OPEN:"Aberto",PENDING:"Pendente",SCHEDULED:"Agendado",RESOLVED:"Resolvido",CLOSED:"Resolvido"};
  async function loadState(code, tab=state.ufTab, pageNumber=state.ticketPage) {
    if (!data) return;
    const allowedTabs=["overview","tickets","projects","providers","reasons","recurrence"];
    if (!allowedTabs.includes(tab)) tab="overview";
    if (state.mode==="base" && tab==="tickets") tab="overview";
    if ((state.mode==="base"||state.mode==="motives") && tab==="recurrence") tab="overview";
    if ((state.mode==="base"||state.mode==="repeat") && tab==="reasons") tab="overview";
    state.ufTab=tab;
    state.ticketPage=pageNumber;
    detailController?.abort();
    const controller=new AbortController(),sequence=++detailSequence;
    detailController=controller;
    const row=data.states[code]||{};
    const url=urlFor(root.dataset.detailUrl,{uf:code,tab:tab,ticket_page:state.ticketPage,ticket_page_size:state.ticketPageSize,ticket_search:state.ticketSearch});
    let detail;
    try{
      const response=await fetch(url,{headers:{"X-Requested-With":"XMLHttpRequest"},signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)])});
      if(!response.ok)throw new Error("Não foi possível abrir os detalhes da UF ("+response.status+").");
      detail=await response.json();
    }catch(error){
      if(controller.signal.aborted||sequence!==detailSequence||state.selectedUF!==code)return;
      throw error;
    }
    if(sequence!==detailSequence||state.selectedUF!==code)return;
    if(isPresentation){
      if(tab==="tickets")tab="overview";
      ui.panelCode.textContent=code;ui.panelName.textContent=stateNames[code]||code;ui.panelFlag.src=flags[code]||"";
      ui.panelTotal.textContent=number.format(metricValue(row));ui.panelScope.textContent=(modeLabels[state.mode]||"Incidentes válidos")+" · "+periodLabel(data.period);
      ui.panelContext.textContent=state.client+" · "+number.format(row.total_links||0)+" links CMDB";
      page.querySelectorAll("[data-panel-section]").forEach(section=>section.hidden=section.dataset.panelSection!==tab);
      page.querySelectorAll("[data-state-tab]").forEach(button=>{button.hidden=button.dataset.stateTab==="tickets";button.setAttribute("aria-selected",String(button.dataset.stateTab===tab));});
      ui.panelOverview.innerHTML=[["Links CMDB",row.total_links],["Incidentes válidos",row.ticket_count],["Links afetados",row.affected_links],["Links reincidentes",row.recurrence_links]].map(([label,amount])=>`<div class="panel-overview-kpi"><strong>${number.format(amount||0)}</strong><span>${esc(label)}</span></div>`).join("");
      listRows(ui.projects,row.project_counts||{});listRows(ui.providers,row.ticket_provider_counts||row.provider_counts||{},"incidentes");listRows(ui.reasons,row.category_counts||{},"incidentes");
      ui.panelRecurrence.textContent=number.format(row.recurrence_links||0)+" links com pelo menos dois incidentes válidos no mês.";
      ui.panelOverviewContext.textContent="Classificações registradas; cenário estimado identificado nas análises.";
      renderClimatePanel(row);openPanel();return;
    }
    ui.panelCode.textContent=code; ui.panelName.textContent=stateNames[code]||code;
    ui.panelFlag.src=flags[code]||""; ui.panelFlag.alt="Bandeira de "+(stateNames[code]||code);
    const count=metricValue(row);
    ui.panelTotal.textContent=number.format(count);
    ui.panelScope.textContent=({rain:"Chuva estimada (mm)",wind:"Rajada amostrada (km/h)",correlation:"Dias de coincid\u00eancia temporal",client:"Links CMDB",calls:"Incidentes v\u00e1lidos",motives:"Incidentes por categoria"}[state.layer]||modeLabels[state.mode])+" \u00b7 "+(state.mode==="base"?"Atual":periodLabel(data.period));
    ui.panelContext.textContent=number.format(row.total_links||0)+" links CMDB \u00b7 "+number.format(row.ticket_count||0)+" tickets no per\u00edodo \u00b7 "+number.format(row.recurrence_links||0)+" links reincidentes.";
    page.querySelectorAll("[data-state-tab]").forEach(button=>{
      const selected=button.dataset.stateTab===tab;
      button.setAttribute("aria-selected",String(selected));
      button.hidden=(state.mode==="base"&&["tickets","reasons","recurrence"].includes(button.dataset.stateTab))
        ||(state.mode==="motives"&&button.dataset.stateTab==="recurrence")
        ||(state.mode==="repeat"&&button.dataset.stateTab==="reasons");
    });
    page.querySelectorAll("[data-panel-section]").forEach(section=>{section.hidden=section.dataset.panelSection!==tab;});
    const projectCounts=["calls","repeat","motives"].includes(state.mode)?(row.ticket_project_counts||{}):(row.project_counts||{});
    const canonicalProjects={};
    Object.entries(projectCounts).forEach(([name,amount])=>{const label=displayProject(name);canonicalProjects[label]=(canonicalProjects[label]||0)+amount;});
    const providerCounts=["calls","repeat","motives"].includes(state.mode)?(row.ticket_provider_counts||{}):(row.provider_counts||{});
    const reasonCounts=state.mode==="calls"?(row.ticket_reason_counts||{}):(row.reason_counts||{});
    if(["projects","calls","repeat","motives","climate"].includes(state.mode)){
      listDimensionRows(ui.projects,row.project_counts,row.ticket_project_counts,displayProject);
      listDimensionRows(ui.providers,row.provider_counts,row.ticket_provider_counts);
    }else{
      listRows(ui.projects,canonicalProjects);
      listRows(ui.providers,providerCounts);
    }
    listRows(ui.reasons,reasonCounts,"tickets");
    const recurrenceRows=detail.recurrences||[];
    ui.panelRecurrence.innerHTML="<div class=\"panel-recurrence-summary\"><strong>"+number.format(row.recurrence_links||0)+"</strong><span>links reincidentes</span><strong>"+number.format(row.recurrence_tickets||0)+"</strong><span>tickets nesses links</span></div>"+
      (recurrenceRows.length?recurrenceRows.map(item=>"<div class=\"panel-reason-row panel-recurrence-row\"><span><strong>"+esc(item.link||"Link")+"</strong><small>"+esc(item.provider||"Provedor não identificado")+"</small></span><b>"+number.format(item.occurrences||0)+" ocorrências<br><small>Última: "+esc(item.last_occurrence?new Date(item.last_occurrence).toLocaleDateString("pt-BR"):"Sem data")+"</small></b></div>").join(""):"<p class=\"panel-empty\">Nenhum link reincidente neste recorte.</p>");
    const projectCount=Object.keys(canonicalProjects).filter(name=>!norm(name).includes("nao identificado")).length;
    const providerCount=Object.keys(providerCounts).filter(name=>!norm(name).includes("nao identificado")).length;
    const ticketCount=state.mode==="motives"?Object.values(row.reason_counts||{}).reduce((a,b)=>a+b,0):state.mode==="repeat"?(row.recurrence_tickets||0):(row.ticket_count||0);
    const kpis=[["Links CMDB",row.total_links||0],["Tickets no recorte",ticketCount],["Links afetados",row.affected_links||row.terminal_links||0],["Projetos",projectCount],["Provedores",providerCount],["Links reincidentes",row.recurrence_links||0]];
    ui.panelOverview.innerHTML=kpis.map(([label,value])=>"<div class=\"panel-overview-kpi\"><strong>"+number.format(value)+"</strong><span>"+esc(label)+"</span></div>").join("");
    const clientCounts=["calls","repeat","motives"].includes(state.mode)?(row.ticket_client_counts||{}):(row.client_counts||{});
    const dominant=topAggregate(clientCounts,["cliente nao identificado"]);
    ui.panelOverviewContext.innerHTML=dominant?"<p>Cliente predominante: <strong>"+esc(dominant[0])+"</strong> \u00b7 "+number.format(dominant[1])+" registros.</p>":"<p>Sem clientes identificados neste recorte.</p>";
    const ticketRows=detail.tickets||[];
    ui.panelTicketSearch.value=state.ticketSearch;
    ui.panelTicketSize.value=String(detail.ticket_page_size||state.ticketPageSize);
    ui.tickets.innerHTML=ticketRows.map(ticket=>{
      const cmdb=ticket.cmdb||{}, identity=ticket.identity||{};
      const created=ticket.created_at?new Date(ticket.created_at).toLocaleDateString("pt-BR"):"Sem data";
      return "<article class=\"panel-ticket-row\"><div><strong>"+esc(ticket.ticket_number||"Chamado")+"</strong><span>"+esc(statusLabels[ticket.status]||ticket.status||"Status n\u00e3o informado")+" \u00b7 "+esc(created)+"</span><p>"+esc(ticket.title||"Sem t\u00edtulo")+"</p><small>"+esc(identity.client||"Cliente n\u00e3o identificado")+" \u00b7 "+esc(identity.project?displayProject(identity.project):"Projeto n\u00e3o identificado")+" \u00b7 "+esc(ticket.link||"Link n\u00e3o identificado")+" \u00b7 "+esc(cmdb.provider||"Provedor n\u00e3o identificado")+"</small><small>Prioridade: "+esc(ticket.priority||"Não informada")+(ticket.closing_reason?" \u00b7 Motivo: "+esc(ticket.closing_reason):"")+"</small></div><div class=\"panel-ticket-arion\"><span>"+esc(ticket.arion_status||"Em Aberto")+"</span><small>"+esc(ticket.assignee||"Sem respons\u00e1vel")+"</small><a class=\"panel-ticket-open\" href=\""+esc(ticket.chamados_url||"#")+"\">Abrir</a></div></article>";
    }).join("")||"<p class=\"panel-empty\">Nenhum chamado corresponde a este recorte.</p>";
    const current=Number(detail.ticket_page||1),pages=Number(detail.ticket_pages||1);
    ui.panelTicketPagination.innerHTML="<span>"+number.format(detail.ticket_total||0)+" chamados \u00b7 P\u00e1gina "+number.format(current)+" de "+number.format(pages)+"</span><div><button type=\"button\" data-panel-ticket-page=\""+Math.max(1,current-1)+"\" "+(current<=1?"disabled":"")+">Anterior</button><button type=\"button\" data-panel-ticket-page=\""+Math.min(pages,current+1)+"\" "+(current>=pages?"disabled":"")+">Pr\u00f3xima</button></div>";
    ui.openBase.href=root.dataset.baseUrl+"?uf="+encodeURIComponent(code)+(state.client!=="all"?"&cliente="+encodeURIComponent(state.client):"")+(state.project?"&projeto="+encodeURIComponent(state.project):"")+(state.provider?"&provider_id="+encodeURIComponent(state.provider):"");
    const cockpit=new URL(root.dataset.cockpitUrl,location.origin);cockpit.searchParams.set("group_by","uf");cockpit.searchParams.set("uf",code);
    cockpit.searchParams.set("metric",state.mode==="motives"?"resolved_closed_tickets":state.mode==="repeat"?"recurrences":"tickets");
    if(data.period?.date_from)cockpit.searchParams.set("start",data.period.date_from);if(data.period?.date_to)cockpit.searchParams.set("end",data.period.date_to);
    if(state.mode==="motives"&&state.reason)cockpit.searchParams.set("termination_reason",state.reason);
    ui.openCockpit.href=cockpit.pathname+cockpit.search;
    ui.openCockpit.textContent=["base","projects"].includes(state.mode)?"Abrir UF no Cockpit":"Analisar no Cockpit";
    renderClimatePanel(row);openPanel();
    ui.announce.textContent=(stateNames[code]||code)+", "+number.format(count)+" "+modeLabels[state.mode]+".";
  }
  function renderClimatePanel(row){
    if(!climateKeys[state.layer])return;
    const value=climateValue(row),c=state.day?row.climate?.daily?.find(d=>d.date===state.day):row.climate;
    const format=(v,unit="")=>Number.isFinite(v)?number.format(v)+(unit?" "+unit:""):"Indisponível";
    const labels={rain:"Chuva estimada · mm",temperature:"Temperatura média · °C",temperature_min:"Temperatura mínima · °C",temperature_max:"Temperatura máxima · °C",wind:"Rajada máxima · km/h",wind_speed:"Vento máximo · km/h",direction:"Direção de origem · °",correlation:"Dias coincidentes · sem causalidade"};
    ui.panelTotal.textContent=format(value);
    ui.panelScope.textContent=labels[state.layer]+" · "+(state.day||state.period);
    const metrics=[["Chuva estimada",c?.[state.day?"precipitation_sum":"precipitation_mm"],"mm"],["Temperatura média",c?.[state.day?"temperature_2m_mean":"temperature_mean_c"],"°C"],["Vento máximo",c?.[state.day?"wind_speed_10m_max":"wind_max_kmh"],"km/h"],["Rajada máxima",c?.[state.day?"wind_gusts_10m_max":"gust_max_kmh"],"km/h"]];
    ui.panelOverview.innerHTML=metrics.map(([label,n,unit])=>`<div class="panel-overview-kpi"><strong>${format(n,unit)}</strong><span>${label}</span></div>`).join("");
    ui.panelOverviewContext.textContent=c?"Estimativa territorial · fonte, modelo e cobertura nas análises.":"Sem cache climático neste recorte. Os chamados permanecem disponíveis.";
  }
  function panelWidth(element,fallback){const width=parseFloat(getComputedStyle(element).width);return Number.isFinite(width)&&width>0?width:fallback;}
  function updateMapLayout(){
    const drawerWidth=panelWidth(panel,440),nationalWidth=panelWidth(national,280);
    root.style.setProperty("--map-drawer-width",`${drawerWidth}px`);
    page.style.setProperty("--geo-detail-reserve",`${state.selectedUF?drawerWidth+52:36}px`);
    root.style.setProperty("--map-national-width",`${nationalWidth}px`);
    root.style.setProperty("--map-control-inset",`${drawerWidth+16}px`);
    const analytics=page.querySelector("[data-geo-intelligence]");
    const surface=page.dataset.rightSurface||"";
    const rightElem=surface==="filters"?page.querySelector(".geo-toolbar--map"):surface.startsWith("analysis-")?analytics:surface==="details"?panel:null;
    const rightFallback=surface==="analysis-summary"?380:460;
    const side=window.innerWidth>760&&surface&&rightElem?panelWidth(rightElem,rightFallback)+22:0;
    const left=window.innerWidth>760&&!state.collapsed?nationalWidth+20:0;
    page.style.setProperty("--geo-left-space",`${left}px`);
    page.style.setProperty("--geo-side-space",`${side}px`);
    root.classList.toggle("national-collapsed",state.collapsed);
    root.classList.toggle("has-selection",Boolean(state.selectedUF));
  }
  function mapFitPadding(focused=Boolean(state.selectedUF)){
    const mobile=window.matchMedia("(max-width:760px)").matches;
    const stageWidth=mapElement.clientWidth||window.innerWidth,stageHeight=mapElement.clientHeight||window.innerHeight;
    let padding;
    if(mobile){
      const drawerHeight=focused?Math.min(panelHeight(),stageHeight*.48):0;
      padding={top:24,right:24,bottom:drawerHeight+24,left:24};
    }else{
      const bottom=page.classList.contains("has-timeline")?62:24;
      padding={top:32,right:28,bottom:Math.min(bottom,stageHeight*.3),left:28};
    }
    return padding;
  }
  function panelHeight(){const height=parseFloat(getComputedStyle(panel).height);return Number.isFinite(height)&&height>0?height:410;}
  function fitSelectedState(duration=650){
    if(!map||!geojson||!state.selectedUF)return;
    const feature=geojson.features.find(item=>item.properties.sigla===state.selectedUF);if(!feature)return;
    const points=[];const walk=value=>Array.isArray(value)&&(typeof value[0]==="number"?points.push(value):value.forEach(walk));walk(feature.geometry.coordinates);

    if(!points.length)return;
    const bounds=points.reduce((box,point)=>box.extend(point),new mapboxgl.LngLatBounds(points[0],points[0]));
    if(smallStateCallouts[state.selectedUF])bounds.extend(smallStateCallouts[state.selectedUF].label);
    map.fitBounds(bounds,{padding:mapFitPadding(true),maxZoom:5.8,duration});
  }
  function fitBrazil(duration=550){fitRegion(duration,true);}
  let manualCamera=false,lastFitScope="";
  function refitCurrentMap(duration=280){if(manualCamera)return;if(state.selectedUF)fitSelectedState(duration);else if(state.region)fitRegion(duration);else fitBrazil(duration);}
  function setNationalCollapsed(collapsed){
    state.collapsed=collapsed;clearTimeout(panelHideTimer);
    if(!collapsed&&window.innerWidth<=1180){
      closeMoreFilters();
      if(rightSurface==="details")closePanel();
      if(rightSurface.startsWith("analysis-"))geoController.close?.();
    }
    if(collapsed){national.inert=true;national.setAttribute("aria-hidden","true");national.classList.add("is-collapsed");$('[data-national-expand]').hidden=false;$("[data-national-collapse]").setAttribute("aria-expanded","false");panelHideTimer=setTimeout(()=>{if(state.collapsed)national.hidden=true;},220);}
    else{national.hidden=false;national.inert=false;national.removeAttribute("aria-hidden");requestAnimationFrame(()=>national.classList.remove("is-collapsed"));$('[data-national-expand]').hidden=true;$("[data-national-collapse]").setAttribute("aria-expanded","true");}
    updateMapLayout();requestAnimationFrame(()=>{map?.resize();refitCurrentMap(280);});
  }
  function openPanel(){
    if(rightSurface!=="details")return;
    syncRightSurface();
  }
  function closePanel(){
    detailSequence++;detailController?.abort();
    if(rightSurface==="details")rightSurface=previousSurface&&previousSurface.startsWith("analysis-")?previousSurface:"";
    syncRightSurface();
  }
  function selectState(code,zoom=true){
    if(!stateNames[code]||!geoController.allowedUFs().includes(code))return;
    manualCamera=false;closeMoreFilters();closeTooltip();
    if(rightSurface!=="details")previousSurface=rightSurface;
    rightSurface="details";syncRightSurface();
    if(state.selectedUF!==code){state.ufTab="overview";state.ticketPage=1;state.ticketSearch="";}
    state.selectedUF=code;syncUrl();ui.breadcrumb.hidden=false;ui.selectedName.textContent=stateNames[code];
    renderActiveFilters();updateMapLayout();renderMap();
    fetchData().catch(error=>showError(error.message));
    if(zoom)fitSelectedState();
  }
  function clearSelection(){closeTooltip();state.selectedUF="";state.ufTab="overview";state.ticketPage=1;state.ticketSearch="";syncUrl();ui.breadcrumb.hidden=true;renderActiveFilters();closePanel();renderMap();if(state.region)fitRegion();else fitBrazil();fetchData().catch(error=>showError(error.message));}
  function updateSearchActive() {
    const options=[...ui.results.querySelectorAll("[data-search-code]")];
    options.forEach((option,index)=>option.setAttribute("aria-selected",String(index===searchIndex)));
    const active=options[searchIndex];
    if(active)ui.search.setAttribute("aria-activedescendant",active.id);
    else ui.search.removeAttribute("aria-activedescendant");
  }
  function searchResults(){
    if(!geojson){closeSearch();return;}
    const query=norm(ui.search.value);
    if(!query){closeSearch();return;}
    const found=geojson.features.filter(feature=>(!(state.region||isPresentation)||(data?.allowed_ufs||[]).includes(feature.properties.sigla))&&(norm(stateNames[feature.properties.sigla]||feature.properties.nome).includes(query)||feature.properties.sigla.toLowerCase().startsWith(query))).slice(0,8);
    ui.results.innerHTML=found.map(feature=>{const code=feature.properties.sigla,name=stateNames[code]||code;return `<button id="state-search-option-${code}" type="button" class="state-search-option" role="option" aria-selected="false" data-search-code="${code}"><img src="${esc(flags[code]||"")}" alt="" width="22" height="15" loading="lazy"><span>${esc(name)}</span><small>${code}</small></button>`;}).join("");
    ui.results.hidden=!found.length;ui.search.setAttribute("aria-expanded",String(found.length>0));searchIndex=-1;page.classList.toggle("is-search-open",found.length>0);updateSearchActive();
  }
  function chooseSearchResult(code){closeSearch(true);closeTooltip();selectState(code);}

  function bind(){
    page.addEventListener("geo:analysis",event=>{
      const st=event.detail?.state;
      rightSurface=st==="expanded"?"analysis-expanded":st==="summary"?"analysis-summary":"";
      closeMoreFilters();syncRightSurface();
    });
    page.querySelector("[data-weather-animate]")?.addEventListener("change",()=>renderMap());
    page.querySelectorAll("[data-mode]").forEach(button=>button.addEventListener("click",()=>{state.mode=button.dataset.mode;if(["base","projects","repeat"].includes(state.mode))state.day="";state.layer=state.mode==="climate"?"rain":state.mode==="motives"?"motives":"analysis";state.ufTab=state.mode==="base"&&state.ufTab!=="overview"?"overview":state.ufTab;updateModeControls();fetchData().catch(error=>showError("N\u00e3o foi poss\u00edvel carregar o recorte selecionado. Tente novamente."));}));
    page.querySelectorAll("[data-client]").forEach(button=>button.addEventListener("click",()=>{state.client=button.dataset.client;state.provider="";$("[data-filter-notice]").textContent="Projeto e ISP revalidados para o cliente selecionado.";page.querySelectorAll("[data-client]").forEach(item=>{const selected=item===button;item.classList.toggle("is-selected",selected);item.setAttribute("aria-pressed",String(selected));});state.project="";ui.project.value="";fetchData().catch(()=>showError("Não foi possível filtrar os dados. Tente novamente."));}));
    ui.project.addEventListener("change",()=>{state.project=ui.project.value;state.provider="";if(isPresentation){state.selectedUF="";state.region="";ui.breadcrumb.hidden=true;closePanel();}$("[data-filter-notice]").textContent="ISP revalidado para o projeto selecionado.";fetchData().catch(()=>showError("Não foi possível filtrar os dados. Tente novamente."));});
    ui.provider.addEventListener("change",()=>{state.provider=ui.provider.value;if(isPresentation){state.selectedUF="";ui.breadcrumb.hidden=true;closePanel();}fetchData().catch(()=>showError("Não foi possível filtrar os dados. Tente novamente."));});
    ui.period.addEventListener("change",()=>{state.period=ui.period.value;if(state.period==="custom"){ui.moreFilters.open=true;syncMoreFiltersOpen();}state.dateFrom="";state.dateTo="";ui.dates.hidden=state.period!=="custom";if(state.period!=="custom")fetchData().catch(()=>showError("Não foi possível carregar o período selecionado."));});
    $("[data-apply-period]").addEventListener("click",()=>{state.dateFrom=ui.dateFrom.value;state.dateTo=ui.dateTo.value;if(state.dateFrom&&state.dateTo&&state.dateFrom>state.dateTo){ui.dateTo.setCustomValidity("A data final deve ser igual ou posterior à inicial.");ui.dateTo.reportValidity();return;}ui.dateTo.setCustomValidity("");fetchData().catch(()=>showError("Não foi possível carregar o período selecionado."));});
    ui.reason.addEventListener("change",()=>{state.reason=ui.reason.value;fetchData().catch(()=>showError("Não foi possível filtrar os motivos."));});
    ui.status.addEventListener("change",()=>{state.status=ui.status.value;fetchData().catch(()=>showError("N\u00e3o foi poss\u00edvel filtrar o status."));});
    ui.priority.addEventListener("change",()=>{state.priority=ui.priority.value;fetchData().catch(()=>showError("N\u00e3o foi poss\u00edvel filtrar a prioridade."));});
    ui.arionStatus.addEventListener("change",()=>{state.arionStatus=ui.arionStatus.value;fetchData().catch(()=>showError("N\u00e3o foi poss\u00edvel filtrar o status ARION."));});
    ui.assignee.addEventListener("change",()=>{state.assignee=ui.assignee.value;fetchData().catch(()=>showError("N\u00e3o foi poss\u00edvel filtrar o respons\u00e1vel."));});
    ui.activeFilters.addEventListener("click",event=>{const clear=event.target.closest("[data-clear-filter]");if(clear){clearFilterValue(clear.dataset.clearFilter);return;}if(event.target.closest("[data-clear-all-filters]")){closeMoreFilters();state.mode="base";state.region="";state.category="";state.layer="analysis";state.client=isPresentation?root.dataset.fixedClient:"all";state.project="";state.provider="";state.period="last_month";state.reason="";state.status="";state.priority="";state.arionStatus="";state.assignee="";state.dateFrom="";state.dateTo="";ui.project.value="";ui.provider.value="";ui.reason.value="";ui.status.value="";ui.priority.value="";ui.arionStatus.value="";ui.assignee.value="";ui.dateFrom.value="";ui.dateTo.value="";page.querySelectorAll("[data-client]").forEach(item=>{const selected=item.dataset.client==="all";item.classList.toggle("is-selected",selected);item.setAttribute("aria-pressed",String(selected));});clearSelection();updateModeControls();fetchData().catch(()=>showError("N\u00e3o foi poss\u00edvel limpar os filtros."));}});
    ui.moreFilters.addEventListener("toggle",syncMoreFiltersOpen);
    page.querySelectorAll("[data-filters-close]").forEach(button=>button.addEventListener("click",()=>closeMoreFilters(true)));
    $("[data-filters-apply]").addEventListener("click",()=>{closeMoreFilters(true);fetchData().catch(error=>showError(error.message));});
    $("[data-filters-clear]").addEventListener("click",()=>ui.activeFilters.querySelector("[data-clear-all-filters]")?.click());
    page.querySelectorAll("[data-state-tab]").forEach(button=>button.addEventListener("click",()=>{if(!state.selectedUF)return;state.ticketPage=1;loadState(state.selectedUF,button.dataset.stateTab,1).catch(error=>showError(error.message,"Detalhes indispon\u00edveis"));}));
    ui.panelTicketSearch.addEventListener("input",()=>{clearTimeout(panelSearchTimer);state.ticketSearch=ui.panelTicketSearch.value.trim();state.ticketPage=1;panelSearchTimer=setTimeout(()=>{if(state.selectedUF&&state.ufTab==="tickets")loadState(state.selectedUF,"tickets",1).catch(error=>showError(error.message,"Detalhes indispon\u00edveis"));},240);});
    ui.panelTicketSize.addEventListener("change",()=>{state.ticketPageSize=Number(ui.panelTicketSize.value)||25;state.ticketPage=1;if(state.selectedUF&&state.ufTab==="tickets")loadState(state.selectedUF,"tickets",1).catch(error=>showError(error.message,"Detalhes indispon\u00edveis"));});
    ui.panelTicketPagination.addEventListener("click",event=>{const button=event.target.closest("[data-panel-ticket-page]");if(!button||button.disabled||!state.selectedUF)return;loadState(state.selectedUF,"tickets",Number(button.dataset.panelTicketPage)).catch(error=>showError(error.message,"Detalhes indispon\u00edveis"));});
    ui.search.addEventListener("input",searchResults);ui.search.addEventListener("keydown",event=>{
      const options=[...ui.results.querySelectorAll("[data-search-code]")];
      if(event.key==="Escape")closeSearch();
      else if(event.key==="ArrowDown"&&options.length){event.preventDefault();searchIndex=searchIndex<0?0:Math.min(options.length-1,searchIndex+1);updateSearchActive();}
      else if(event.key==="ArrowUp"&&options.length){event.preventDefault();searchIndex=searchIndex<0?options.length-1:Math.max(0,searchIndex-1);updateSearchActive();}
      else if(event.key==="Home"&&options.length){event.preventDefault();searchIndex=0;updateSearchActive();}
      else if(event.key==="End"&&options.length){event.preventDefault();searchIndex=options.length-1;updateSearchActive();}
      else if(event.key==="Enter"){const option=options[searchIndex]||options[0];if(option){event.preventDefault();chooseSearchResult(option.dataset.searchCode);}}
      else if(event.key==="Tab")closeSearch();
    });
    ui.results.addEventListener("click",event=>{const button=event.target.closest("[data-search-code]");if(button)chooseSearchResult(button.dataset.searchCode);});
    document.addEventListener("pointerdown",event=>{if(ui.moreFilters.open&&!ui.moreFilters.contains(event.target))closeMoreFilters();if(!event.target.closest(".geo-search-wrap")&&!ui.results.hidden)closeSearch();});
    document.addEventListener("keydown",event=>{if(event.key==="Escape"&&ui.moreFilters.open){event.preventDefault();event.stopPropagation();closeMoreFilters(true);}});
    $("[data-national-collapse]").addEventListener("click",()=>setNationalCollapsed(true));
    $("[data-national-expand]").addEventListener("click",()=>setNationalCollapsed(false));
    page.querySelectorAll("[data-national-tab]").forEach(button=>button.addEventListener("click",()=>setPanelTab(button.dataset.nationalTab)));
    $("[data-brazil-link]").addEventListener("click",clearSelection);$("[data-panel-close]").addEventListener("click",closePanel);
    const moveState=step=>{const order=stateOrder.filter(code=>!(state.region||isPresentation)||(data?.allowed_ufs||[]).includes(code));const index=order.indexOf(state.selectedUF);if(index<0)return;selectState(order[(index+step+order.length)%order.length]);};
    ui.ufPrevious.addEventListener("click",()=>moveState(-1));ui.ufNext.addEventListener("click",()=>moveState(1));
    $("[data-map-retry]").addEventListener("click",initialize);
    window.addEventListener("arion:themechange",event=>{const next=event.detail?.theme||theme();if(map?.getLayer("arion-background"))map.setPaintProperty("arion-background","background-color",next==="light"?"#e5eae6":"#0c0d0d");renderMap();renderLegend();});
    let layoutTimer=0;
    page.addEventListener("geo:layout",()=>{clearTimeout(layoutTimer);layoutTimer=setTimeout(()=>{map?.resize();refitCurrentMap(0);},90);});
    let resizeTimer=0;
    window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(ui.moreFilters.open)syncMoreFiltersOpen();if(!map)return;updateMapLayout();map.resize();refitCurrentMap(260);},140);},{passive:true});
  }
  async function initialize(){
    if(!root.dataset.token){showError("Token público do Mapbox não configurado.");return;}
    if(!window.mapboxgl){showError("Não foi possível carregar o componente do mapa. Verifique a conexão.");return;}
    if(map){closeTooltip();map.remove();map=null;mapLoaded=false;}
    message.hidden=false;errorBox.hidden=true;
    try{
      const response=await fetch(root.dataset.geojsonUrl,{signal:AbortSignal.timeout(20000)});if(!response.ok)throw new Error("Não foi possível carregar as geometrias locais.");geojson=await response.json();
      if(geojson.type!=="FeatureCollection"||geojson.features.length!==27)throw new Error("A base geográfica precisa conter as 27 UFs.");
      await fetchData();window.mapboxgl.accessToken=root.dataset.token;
      map=new window.mapboxgl.Map({container:mapElement,style:{version:8,glyphs:"mapbox://fonts/mapbox/{fontstack}/{range}.pbf",sources:{},layers:[{id:"arion-background",type:"background",paint:{"background-color":theme()==="light"?"#e5eae6":"#0c0d0d"}}]},bounds:[[-76,-37],[-32,8]],fitBoundsOptions:{padding:35},minZoom:1.3,maxZoom:8,renderWorldCopies:false,attributionControl:true,cooperativeGestures:false});
      const canvas=map.getCanvas();
      map.on("movestart",event=>{if(event.originalEvent)manualCamera=true;});
      mapElement.addEventListener("pointerdown",event=>{if(event.target.closest(".mapboxgl-ctrl-zoom-in,.mapboxgl-ctrl-zoom-out"))manualCamera=true;});
      canvas.addEventListener("wheel",()=>{manualCamera=true;},{passive:true});
      canvas.addEventListener("pointermove",event=>{if(event.buttons)manualCamera=true;},{passive:true});
      canvas.addEventListener("keydown",event=>{if(["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","-","="].includes(event.key))manualCamera=true;});
      map.addControl(new window.mapboxgl.NavigationControl({showCompass:false}),"top-right");map.dragRotate.disable();map.touchZoomRotate.disableRotation();
      map.on("load",()=>{try{
        map.addSource("arion-states",{type:"geojson",data:featureCollection(),promoteId:"sigla"});
        map.addLayer({id:"arion-state-fill",type:"fill",source:"arion-states",paint:{"fill-color":fillExpression(),"fill-opacity":["case",["boolean",["feature-state","selected"],false],.98,["boolean",["get","has_data"],false],.92,.48],"fill-color-transition":{duration:320,delay:0},"fill-opacity-transition":{duration:200,delay:0}}});
        map.addLayer({id:"arion-state-selected-halo",type:"line",source:"arion-states",paint:{"line-color":theme()==="light"?"#789382":"#c3d8c8","line-width":6,"line-opacity":["case",["boolean",["feature-state","selected"],false],.2,0],"line-blur":1.2,"line-width-transition":{duration:180,delay:0},"line-opacity-transition":{duration:180,delay:0}}});
        map.addLayer({id:"arion-state-outline",type:"line",source:"arion-states",paint:{"line-color":["case",["boolean",["feature-state","selected"],false],theme()==="light"?"#496454":"#e3eee5",["boolean",["feature-state","hover"],false],theme()==="light"?"#718579":"#d1ded4",theme()==="light"?"rgba(48,67,56,.36)":"rgba(221,233,224,.34)"],"line-width":["case",["boolean",["feature-state","selected"],false],2.2,["boolean",["feature-state","hover"],false],1.6,.8],"line-color-transition":{duration:180,delay:0},"line-width-transition":{duration:180,delay:0}}});
        map.addSource("arion-state-label-points",{type:"geojson",data:labelCollection()});
        map.addLayer({id:"arion-weather-labels",type:"symbol",source:"arion-state-label-points",minzoom:3.2,layout:{"text-field":["get","weather_label"],"text-font":["Open Sans Semibold","Arial Unicode MS Regular"],"text-size":10,"text-offset":[0,1.4],"text-allow-overlap":false},paint:{"text-color":theme()==="light"?"#25372f":"#e4ebe5","text-halo-color":theme()==="light"?"#e5eae6":"#151b18","text-halo-width":1.5}});
        map.addLayer({id:"arion-weather-direction",type:"symbol",source:"arion-state-label-points",filter:["==",["get","directional"],true],layout:{"text-field":"↓","text-size":22,"text-rotate":["get","wind_direction"],"text-offset":[0,-1],"text-allow-overlap":false},paint:{"text-color":theme()==="light"?"#67508f":"#d0b3ef"}});
        map.addLayer({id:"arion-state-labels",type:"symbol",source:"arion-state-label-points",layout:{"text-field":["get","sigla"],"text-font":["Open Sans Semibold","Arial Unicode MS Regular"],"text-size":["interpolate",["linear"],["zoom"],2,11,5.8,14],"text-allow-overlap":false,"text-ignore-placement":true,"text-optional":true,"symbol-placement":"point"},paint:{"text-color":theme()==="light"?"#25372f":"#e4ebe5","text-halo-color":theme()==="light"?"rgba(235,240,235,.94)":"rgba(17,22,20,.94)","text-halo-width":1.2,"text-opacity":.9}});
        map.addSource("arion-state-callouts",{type:"geojson",data:calloutCollection(),promoteId:"sigla"});
        map.addLayer({id:"arion-state-callout-lines",type:"line",source:"arion-state-callouts",filter:["==",["geometry-type"],"LineString"],paint:{"line-color":theme()==="light"?"rgba(48,67,56,.5)":"rgba(221,233,224,.45)","line-width":1,"line-opacity":.88}});
        map.addLayer({id:"arion-state-callout-labels",type:"symbol",source:"arion-state-callouts",filter:["==",["geometry-type"],"Point"],layout:{"text-field":["get","sigla"],"text-font":["Open Sans Semibold","Arial Unicode MS Regular"],"text-size":["interpolate",["linear"],["zoom"],2,11,5.8,14],"text-allow-overlap":false,"text-ignore-placement":true,"text-optional":true,"symbol-placement":"point","text-padding":5},paint:{"text-color":theme()==="light"?"#31463a":"#e5ece6","text-halo-color":theme()==="light"?"#f2f5f1":"#151b18","text-halo-width":3,"text-halo-blur":.15,"text-opacity":1}});
        const selectableLayers=["arion-state-fill","arion-state-labels","arion-state-callout-labels"];
        selectableLayers.forEach(layer=>{
          map.on("click",layer,event=>{if(map.getLayer("arion-weather-icon-points")&&map.queryRenderedFeatures(event.point,{layers:["arion-weather-icon-points","arion-weather-icon-clusters"]}).length)return;const code=event.features?.[0]?.properties?.sigla;if(code)selectState(code);});
          map.on("mouseenter",layer,()=>map.getCanvas().style.cursor="pointer");
          map.on("mousemove",layer,event=>{const code=event.features?.[0]?.properties?.sigla||"";if(code!==hoveredUF){if(hoveredUF)setFeatureState(hoveredUF,"hover",false);hoveredUF=code;if(code)setFeatureState(code,"hover",true);}showTooltip(event,code);});
          map.on("mouseleave",layer,()=>{map.getCanvas().style.cursor="";if(hoveredUF)setFeatureState(hoveredUF,"hover",false);hoveredUF="";deferCloseTooltip();});
        });
        map.on("mouseenter","arion-state-callout-lines",()=>map.getCanvas().style.cursor="pointer");
        map.on("click","arion-state-callout-lines",event=>{const code=event.features?.[0]?.properties?.sigla;if(code)selectState(code);});
        map.on("mousemove","arion-state-callout-lines",event=>{const code=event.features?.[0]?.properties?.sigla||"";if(code!==hoveredUF){if(hoveredUF)setFeatureState(hoveredUF,"hover",false);hoveredUF=code;if(code)setFeatureState(code,"hover",true);}showTooltip(event,code);});
        map.on("mouseleave","arion-state-callout-lines",()=>{map.getCanvas().style.cursor="";if(hoveredUF)setFeatureState(hoveredUF,"hover",false);hoveredUF="";deferCloseTooltip();});
        map.on("dragstart",e=>{if(e.originalEvent)manualCamera=true;});map.on("zoomstart",e=>{if(e.originalEvent)manualCamera=true;});
        weatherIcons=mountWeatherIcons({map,page,state,onOpen:closeTooltip,positions:{...stateLabelCoordinates,...Object.fromEntries(Object.entries(smallStateCallouts).map(([uf,p])=>[uf,p.anchor]))},regionForUF:uf=>({1:"N",2:"NE",3:"SE",4:"S",5:"CO"}[lastWeatherPayload?.catalog.states.find(row=>row.sigla===uf)?.region_id]||"SE")});
        if(lastWeatherPayload)weatherIcons.update(lastWeatherPayload,state.day);
        mapLoaded=true;message.hidden=true;renderMap();updateMapLayout();if(state.selectedUF)selectState(state.selectedUF);else refitCurrentMap();
      }catch(_error){showError("Não foi possível preparar as unidades do mapa.");}});
      map.on("error",()=>{if(!mapLoaded)showError("Confira a configuração pública e a conexão do Mapbox.");});
    }catch(_error){showError("Não foi possível carregar os dados locais do Mapa. Tente novamente.");}
  }
  function topAggregate(values, excluded=[]) {
    return Object.entries(values||{}).filter(([label,count])=>count>0&&!excluded.includes(norm(label)))
      .sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0],"pt-BR"))[0];
  }
  function tooltipLine(label,value) {
    if (value === undefined || value === null || value === "") return "";
    return `<div class="tooltip-data-line"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
  }
  function positionTooltip(point){
    if(!point)return;
    const bounds=root.getBoundingClientRect(),width=ui.hoverTooltip.offsetWidth,height=ui.hoverTooltip.offsetHeight;
    const margin=10,gap=16,maxX=Math.max(margin,root.clientWidth-width-margin),maxY=Math.max(margin,root.clientHeight-height-margin);
    const candidates=[
      [point.x+gap,point.y+gap],[point.x-width-gap,point.y+gap],
      [point.x+gap,point.y-height-gap],[point.x-width-gap,point.y-height-gap],
    ];
    const blockers=[];
    if(!national.hidden&&!state.collapsed&&!national.classList.contains("is-collapsed"))blockers.push(national);
    if(state.selectedUF&&panel.classList.contains("is-open"))blockers.push(panel);
    const boxes=blockers.map(element=>{const rect=element.getBoundingClientRect();return {left:rect.left-bounds.left,top:rect.top-bounds.top,right:rect.right-bounds.left,bottom:rect.bottom-bounds.top};});
    let selected=null,bestScore=Infinity;
    for(const [rawX,rawY] of candidates){
      const x=Math.min(maxX,Math.max(margin,rawX)),y=Math.min(maxY,Math.max(margin,rawY));
      const rect={left:x,top:y,right:x+width,bottom:y+height};
      const score=boxes.reduce((total,box)=>total+Math.max(0,Math.min(rect.right,box.right)-Math.max(rect.left,box.left))*Math.max(0,Math.min(rect.bottom,box.bottom)-Math.max(rect.top,box.top)),0);
      if(score<bestScore){selected={x,y};bestScore=score;if(score===0)break;}
    }
    ui.hoverTooltip.style.left=`${selected.x}px`;ui.hoverTooltip.style.top=`${selected.y}px`;
  }
  function buildTooltip(code,row) {
    const title=stateNames[code]||code, amount=metricValue(row);
    const quantity=number.format(amount);
    const metricLabel=state.mode==="base"||state.mode==="projects"?(amount===1?"link":"links"):
      state.mode==="repeat"?(amount===1?"link reincidente":"links reincidentes"):
      state.mode==="motives"?(amount===1?"encerramento":"encerramentos"):(amount===1?"ticket":"tickets");
    const project=topAggregate(row.project_counts,["projeto nao identificado"]);
    const projectCounts=["calls","repeat","motives"].includes(state.mode)?(row.ticket_project_counts||{}):(row.project_counts||{});
    const projectTotal=Object.keys(projectCounts).filter(value=>!["projeto nao identificado",""].includes(norm(value))).length;
    const provider=topAggregate(["calls","motives"].includes(state.mode)?row.ticket_provider_counts:state.mode==="repeat"?row.recurrence_provider_counts:row.provider_counts,["provedor nao identificado"]);
    const reason=topAggregate(state.mode==="calls"?row.ticket_reason_counts:row.reason_counts);
    let rows="";
    if (state.mode==="base") {
      if (amount) rows+=tooltipLine("Cliente predominante",dominantClientLabel(row));
      if (amount) rows+=tooltipLine("Cobertura de cliente",`${Number(row.client_coverage||0).toLocaleString("pt-BR",{maximumFractionDigits:1})}%`);
      if (project) rows+=tooltipLine("Projeto principal",displayProject(project[0]));
      if (provider) rows+=tooltipLine("Provider principal",provider[0]);
    } else if (state.mode==="projects") {
      const projectName=state.project?displayProject(state.project):project?displayProject(project[0]):"";
      rows+=tooltipLine("Projetos na UF",number.format(projectTotal));
      if (projectName) rows+=tooltipLine("Projeto",projectName);
      if(project)rows+=tooltipLine("Participação",`${(100*project[1]/Math.max(1,row.total_links)).toFixed(1)}% dos links da UF`);
      rows+=tooltipLine("Links",number.format(row.total_links||0));
      rows+=tooltipLine("Tickets no período",number.format(row.ticket_count||0));
      if (amount) rows+=tooltipLine("Cliente predominante",dominantClientLabel(row));
      if (provider) rows+=tooltipLine("Provider principal",provider[0]);
    } else if (state.mode==="calls") {
      const statuses=row.ticket_status_counts||{};
      const active=(statuses.OPEN||0)+(statuses.PENDING||0)+(statuses.SCHEDULED||0);
      const resolved=(statuses.RESOLVED||0)+(statuses.CLOSED||0);
      rows+=tooltipLine("Abertos / em andamento",number.format(active));
      rows+=tooltipLine("Resolvidos / fechados",number.format(resolved));
      rows+=tooltipLine("Links afetados",`${number.format(row.affected_links||0)} ${(row.affected_links||0)===1?"link":"links"}`);
      rows+=tooltipLine("Links reincidentes",number.format(row.recurrence_links||0));
      if (reason) rows+=tooltipLine("Principal motivo",reason[0]);
      if (provider) rows+=tooltipLine("Provider principal",provider[0]);
    } else if (state.mode==="repeat") {
      rows+=tooltipLine("Tickets nesses links",number.format(row.recurrence_tickets||0));
      if (provider) rows+=tooltipLine("Provider principal",provider[0]);
    } else {
      const terminalLinks=row.terminal_links||row.terminal_link_count||0;
      rows+=tooltipLine("Links afetados",`${number.format(terminalLinks)} ${terminalLinks===1?"link":"links"}`);
      if (provider) rows+=tooltipLine("Provider principal",provider[0]);
      if (!state.reason&&reason) rows+=tooltipLine("Principal motivo",reason[0]);
    }
    const flag=flags[code]||"";
    return `<div class="state-tooltip"><div class="tooltip-heading"><img src="${esc(flag)}" alt="" width="28" height="19" decoding="async"><div><strong>${esc(title)}</strong><span>${esc(code)} &#183; Brasil</span></div></div><div class="tooltip-primary-metric"><strong>${quantity}</strong><span>${esc(state.mode==="motives"&&state.reason?reasonLabel():metricLabel)}</span></div>${rows?`<div class="tooltip-details">${rows}</div>`:""}</div>`;
  }
  function showTooltip(event,codeOverride=""){
    if(page.querySelector(".mapboxgl-popup")||map?.getLayer("arion-weather-icon-points")&&event.point&&map.queryRenderedFeatures(event.point,{layers:["arion-weather-icon-points","arion-weather-icon-clusters"]}).length){closeTooltip();return;}
    clearTimeout(tooltipCloseTimer);
    const code=codeOverride||event.features?.[0]?.properties?.sigla;if(!code){closeTooltip();return;}
    if(code===state.selectedUF){closeTooltip();return;}
    const row=data?.states?.[code]||{};
    if(state.layer==="client"||state.layer==="motives"){
      const counts=row.geo_category_counts||row.category_counts||{};
      const cause=Object.entries(counts).sort((a,b)=>b[1]-a[1])[0];
      if(state.layer==="client"){
        ui.hoverTooltip.innerHTML=`<div class="state-tooltip"><strong>${esc(stateNames[code])}</strong><p>Links CMDB \u00b7 ${number.format(metricValue(row))}</p></div>`;
      }else{
        const cname=cause?.[0]||"Sem dados de categoria";
        const ufTotal=Object.values(counts).reduce((a,b)=>a+b,0);
        const pct=ufTotal&&cause?` (${(cause[1]/ufTotal*100).toLocaleString("pt-BR",{maximumFractionDigits:1})}%)`:"";
        ui.hoverTooltip.innerHTML=`<div class="state-tooltip"><strong>${esc(stateNames[code])}</strong><p><span style="display:inline-flex;align-items:center;gap:6px;"><i style="width:8px;height:8px;border-radius:50%;background:${causeColor(cname,theme())};display:inline-block;"></i><kbd class="geo-legend-code">${causeBadge(cname)}</kbd>${esc(cname)}</span> \u00b7 ${number.format(cause?.[1]??metricValue(row))}${pct}</p></div>`;
      }
      ui.hoverTooltip.hidden=false;positionTooltip(event.point);return;
    }
    if(climateKeys[state.layer]){
      const labels={temperature:"Temperatura média · °C",temperature_min:"Temperatura mínima · °C",temperature_max:"Temperatura máxima · °C",wind_speed:"Vento · km/h",direction:"Direção dominante · °",rain:"Chuva estimada (mm)",wind:"Rajada máxima amostrada (km/h)",correlation:"Dias coincidentes · sem causalidade"};
      ui.hoverTooltip.innerHTML=`<div class="state-tooltip"><strong>${esc(stateNames[code])}</strong><p>${esc(labels[state.layer])}: ${Number.isFinite(climateValue(row))?number.format(metricValue(row)):"Indisponível"}</p></div>`;
      ui.hoverTooltip.hidden=false;tooltipPoint=event.point;positionTooltip(tooltipPoint);return;
    }
    const key=[code,state.mode,state.reason,state.project,metricValue(row)].join("|");
    if(key!==tooltipKey){ui.hoverTooltip.innerHTML=buildTooltip(code,row);tooltipKey=key;}
    ui.hoverTooltip.hidden=false;
    tooltipPoint=event.point?{x:event.point.x,y:event.point.y}:map.project(event.lngLat);
    if(!tooltipMoveFrame)tooltipMoveFrame=requestAnimationFrame(()=>{tooltipMoveFrame=0;if(!ui.hoverTooltip.hidden)positionTooltip(tooltipPoint);});
  }

  function fitRegion(duration=650,national=false){
    if(!map||!geojson)return;
    const features=geojson.features.filter(f=>national||!state.region||geoController.allowedUFs().includes(f.properties.sigla));
    if(!features.length)return;
    const bounds=new window.mapboxgl.LngLatBounds();
    const walk=coords=>{if(typeof coords[0]==="number")bounds.extend(coords);else coords.forEach(walk);};
    features.forEach(f=>walk(f.geometry.coordinates));
    const codes=new Set(features.map(f=>f.properties.sigla));
    Object.entries(smallStateCallouts).forEach(([code,p])=>{if(codes.has(code))bounds.extend(p.label);});
    map.fitBounds(bounds,{padding:mapFitPadding(false),maxZoom:6,duration:window.matchMedia("(prefers-reduced-motion: reduce)").matches?0:duration});
  }
  const geoController=mountGeoIntelligence({page,root,state,urlFor,refresh:fetchData,fitRegion:()=>setTimeout(fitRegion,100),selectUF:code=>code?selectState(code):clearSelection(),onFilterUpdate:()=>updateModeControls(),onWeather:(payload,day)=>{
    lastWeatherPayload=payload;weatherIcons?.update(payload,day);
    state.day=day||"";
    if(!data)return;
    data.geo_daily_counts=payload.summary.daily_counts;
    data.geo_climate=day?payload.daily.find(r=>r.date===day):payload.climate;
    for(const code of Object.keys(payload.weather))if(!data.states[code])data.states[code]={total_links:0,ticket_count:0};
    if(["base","projects"].includes(state.mode)){data.ticket_count=payload.summary.ticket_count;data.affected_link_count=payload.summary.affected_link_count;}
    Object.entries(data.states).forEach(([code,row])=>{row.climate=payload.weather[code];row.geo_ticket_count=payload.summary.states[code]?.ticket_count||0;row.geo_category_counts=payload.summary.states[code]?.category_counts||{};row.geo_daily_counts=payload.summary.states[code]?.daily_counts||{};if(["base","projects"].includes(state.mode))row.ticket_count=row.geo_ticket_count;});
    renderMap();renderLegend();renderNational();
    if(state.selectedUF)renderClimatePanel(data.states[state.selectedUF]||{});
  }});
  updateModeControls();
  page.querySelectorAll("[data-client]").forEach(item=>{const selected=item.dataset.client===state.client;item.classList.toggle("is-selected",selected);item.setAttribute("aria-pressed",String(selected));});
  bind();setNationalCollapsed(window.innerWidth<=760);initialize();
  const resizeBand=()=>{
    const controls=page.querySelector(".geo-analysis-controls"),actions=page.querySelector("[data-top-actions]"),filters=page.querySelector(".geo-filter-panel"),subband=page.querySelector("[data-top-subband]");
    const offset=isPresentation?64:0;
    const row1Bottom=Math.max(
      controls?controls.offsetTop+controls.offsetHeight:0,
      actions?actions.offsetTop+actions.offsetHeight:0,
      filters?filters.offsetTop+filters.offsetHeight:0
    );
    if(subband){
      subband.style.top=`${row1Bottom+6}px`;
      page.style.setProperty("--geo-band-height",`${subband.offsetTop+subband.offsetHeight+8-offset}px`);
    }else{
      page.style.setProperty("--geo-band-height",`${row1Bottom+10-offset}px`);
    }
    if(window.innerWidth<=1180&&!state.collapsed&&rightSurface)setNationalCollapsed(true);
    updateMapLayout();
  };
  new ResizeObserver(resizeBand).observe(page.querySelector(".geo-analysis-controls"));
  if(page.querySelector("[data-top-subband]"))new ResizeObserver(resizeBand).observe(page.querySelector("[data-top-subband]"));
  if(page.querySelector("[data-top-actions]"))new ResizeObserver(resizeBand).observe(page.querySelector("[data-top-actions]"));
  new ResizeObserver(resizeBand).observe(ui.legend);
  if(window.ResizeObserver){
    let layoutFrame=0;
    new ResizeObserver(()=>{cancelAnimationFrame(layoutFrame);layoutFrame=requestAnimationFrame(()=>{if(map){map.resize();refitCurrentMap(0);}});}).observe(mapElement);
  }
})();
