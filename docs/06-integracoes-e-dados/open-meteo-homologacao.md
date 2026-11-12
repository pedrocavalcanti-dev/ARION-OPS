# Open-Meteo — configuração e ensaio GEO pela aplicação

Estado verificado em 12/11/2026 neste checkout DEV: SQLite, sem cadastro `open_meteo`, modalidade, autorização de avaliação, licença ou chave cadastradas. Zero jobs e zero caches meteorológicos. Supervisor: `worker: stopped`; nenhum processo `integration_worker` encontrado; leases `worker` e `geo-climate` vencidos, sem heartbeat. Bootstrap de banco inexistente, sem candidato MySQL ou `DATABASE_URL`; `arion_geo_dev` não disponível. Nenhum serviço de produção ou OTRS foi utilizado para esta validação.

**O fluxo real SP / outubro de 2026 ainda não foi homologado.** O impedimento ocorre antes do acesso externo: falta cadastrar uma modalidade autorizada e iniciar o Worker no ambiente de homologação. Uma resposta HTTP obtida diretamente para a capital não comprova coleta territorial, fila ou persistência pela aplicação.

## 1. Cadastrar e habilitar a integração

Com `configuracoes.view_integrations` e `configuracoes.manage_integrations`, abrir Central de Configurações → Integrações → Open-Meteo, em `/configuracoes/integracoes/geo/open_meteo/`. O formulário já permite criar o cadastro; não é necessário inserir registros pelo shell ou pelo Django admin.

Escolher a modalidade conforme a autorização do responsável:

| Modalidade implementada | Condições exigidas pela aplicação |
|---|---|
| Avaliação / homologação | Ambiente fora de produção, uso não comercial autorizado e confirmação explícita no campo de avaliação. Endpoint `https://archive-api.open-meteo.com/v1/archive`. Atualização automática desmarcada. |
| Comercial licenciada | Contrato real com acesso ao histórico compatível com o requisito do formulário, confirmação da licença e chave válida. Endpoint `https://customer-archive-api.open-meteo.com/v1/archive`. Validar a elegibilidade contratual com o fornecedor. |

Para a primeira coleta, manter atualização automática desmarcada, configurar orçamento HTTP suficiente para a amostragem territorial de SP e suas tentativas, marcar **Habilitar integração** e salvar. Campos numéricos possuem valores iniciais e limites no formulário. Não marcar autorizações/licença como forma de eliminar uma mensagem de erro.

Chaves comerciais são protegidas por DPAPI. A identidade Windows que salva a chave deve ser compatível com a identidade do serviço Web/Worker que irá descriptografá-la. O frontend exibe apenas o estado da credencial. Nenhum segredo precisa ser publicado em documentação ou Git.

## 2. Verificar o Worker e o contrato real

No ambiente DEV autorizado, com a mesma configuração de banco usada pela Web:

```powershell
.venv\Scripts\python.exe deploy\windows\run_process.py --status worker
```

Se estiver parado, o operador autorizado inicia pelo supervisor existente:

```powershell
.\deploy\windows\start-arion.ps1 -Role worker
.venv\Scripts\python.exe deploy\windows\run_process.py --status worker
```

Esse Worker também agenda outras integrações; conferir a configuração DEV antes de iniciá-lo. Esta tarefa **não iniciou o Worker**. Na Central, usar **Testar conectividade** com `configuracoes.run_integrations`. O teste consulta uma célula territorial de SP para o último mês completo e valida as variáveis e unidades; não salva essa célula como estimativa estadual. Respeitar o intervalo mínimo de um minuto entre testes. Registrar sucesso e latência no histórico de saúde. Em falha, corrigir autorização, plano, endpoint, chave/DPAPI ou conectividade antes de continuar.

## 3. Solicitar somente SP / 2026-10

No Mapa interno, escolher Clima, mês **outubro de 2026**, UF **SP** e cliente/projeto autorizados que possuam links em SP. Aplicar e abrir **Análises**. Conferir os chips de UF/mês e então usar **Coletar clima do recorte**. A ação exige `view_map`, `run_integrations`, sessão e CSRF; o backend determina as UFs elegíveis após Data Scope e filtros. Selecionar SP antes da solicitação evita enfileirar o Brasil inteiro.

Sem configuração válida, a tela oferece **Configurar integração** apenas a administradores com acesso à configuração. O POST mantém HTTP 409 / `disabled` para cadastro ausente, acompanhado de `configuration_status=not_configured`. Não há bypass de licença nem inserção de cache fictício.

## 4. Confirmar fila, persistência e retorno ao Mapa

Na Central, acompanhar SP / 10/2026 em **Coletas recentes / pendentes**: pendente → coletando → sucesso. Jobs concluídos saem da lista de pendências. O Mapa acompanha fila/processamento a cada 15 segundos, lendo somente o banco. Sem lease ativo, informa que o Worker precisa ser verificado. Falhas conservam cache anterior e usam tentativas com backoff; consultar a saúde da integração.

Para confirmação técnica somente de leitura, no banco DEV correto:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py shell -c "from aplicativos.integracoes.models import WeatherCollectionJob, WeatherMonth; print(list(WeatherCollectionJob.objects.filter(uf='SP',period='2026-10-01').values('uf','period','status','attempts','updated_at'))); print(list(WeatherMonth.objects.filter(uf='SP',period='2026-10-01',method='area-grid-v2').values('uf','period','source','model','method','completeness','collected_at','expires_at')))"
```

Exigir `status=success`, cache do método `area-grid-v2`, fonte correspondente à modalidade, modelo ERA5, datas e cobertura coerentes. Reabrir/atualizar as análises e confirmar chuva em mm, temperatura em °C e vento/rajada em km/h. Alternar camadas, selecionar SP, expandir a série mensal e reproduzir dias. Zero observado deve permanecer diferente de ausência; a reprodução reutiliza séries locais, sem chamadas externas por frame. Coincidência usa apenas classificações técnicas conhecidas e não declara causalidade.

## Checklist de aceite externo pendente

- [ ] MySQL DEV `arion_geo_dev` configurado e validado, sem conexão com produção.
- [ ] Modalidade permitida e autorização/contrato confirmados pelo responsável.
- [ ] Chave e identidade DPAPI verificadas, quando comerciais.
- [ ] Teste real pela Central aprovado.
- [ ] Worker ativo e heartbeat atual no mesmo DEV da Web.
- [ ] Uma solicitação SP / 2026-10 processada com sucesso.
- [ ] Cache `v2` persistido e indicadores reais visíveis no Mapa.
- [ ] Chuva, temperatura, vento/rajada e reprodução diária conferidos com o cache real.

Os testes automatizados usam contratos controlados em banco de testes. A revisão visual usa snapshot SQLite isolado; não comprova conexão meteorológica real. Ver [candidata 1.4.1](../08-releases/v1.4.1-candidata.md) e [procedimento de implantação](../08-releases/geo-intelligence-implantacao-windows.md).
