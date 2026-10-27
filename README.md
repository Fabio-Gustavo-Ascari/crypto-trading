# Crypto Trading Harness

MVP local com início e parada manuais. Python coleta, protege posições e registra
resultados; Codex escolhe OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT, HOLD ou REJECT.

Nesta máquina, o ambiente virtual já foi preparado. Você pode abrir
`Testar-sem-IA.cmd`, `Iniciar.cmd`, `Parar.cmd` e `Relatorio.cmd` diretamente.
Os comandos abaixo também funcionam no terminal.

## Testar no Windows

Abra PowerShell na pasta do projeto. Ambiente virtual recomendado:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m crypto_harness once --observe
```

`--observe` consulta dados reais e registra HOLD, sem consumir IA. Depois de
`codex login`, teste uma decisão com:

```powershell
.\.venv\Scripts\python.exe -m crypto_harness once --max-calls 1
```

CLI Codex precisa estar no PATH. Integração preparada para CLI 0.162.1. `--model NOME`
escolhe um modelo disponível na conta; omitido usa `gpt-6-luna`. A variável
`CRYPTO_HARNESS_MODEL` define o padrão local e `CRYPTO_HARNESS_FALLBACK_MODELS`
aceita uma lista separada por vírgulas (padrão: `gpt-5.6-luna`). Cada tentativa,
inclusive stderr e categoria de falha, vai para o journal; HOLD por falha só ocorre
depois que todos os modelos configurados falharem.
local desta conta, com esforço baixo. Se não estiver disponível em outra conta,
selecione outro com `--model`.

## Iniciar e parar

```powershell
.\.venv\Scripts\python.exe -m crypto_harness start
# Em outro terminal, na mesma pasta:
.\.venv\Scripts\python.exe -m crypto_harness status
.\.venv\Scripts\python.exe -m crypto_harness stop
.\.venv\Scripts\python.exe -m crypto_harness report
```

O processo fica no terminal até `stop` ou Ctrl+C. Mantenha o PC acordado e conectado.
Não depende do Desktop aberto. Não há agendamento ou serviço instalado.
IA a cada 15 minutos; monitor independente a cada 30 segundos. Timeout da IA: 90s.
Parada pode levar cerca de dois minutos com chamada em andamento.

Padrão: **8 chamadas por execução e 8 por dia de Brasília**, persistidas em SQLite,
incluindo falhas. Ao atingir o teto, continua monitorando sem IA e registra HOLD.
Três falhas consecutivas suspendem IA naquela execução. O teto diário renova no
próximo dia. Não é teto de tokens/créditos: consumo por chamada varia. Uso retornado
pelo CLI fica no journal. Para limitar mais:

```powershell
.\.venv\Scripts\python.exe -m crypto_harness start --max-calls 4 --max-daily-calls 4
```

`once` encerra após um ciclo. Ao encerrar, tenta fechar posições com preços recentes.
Se não conseguir, preserva posições e avisa que o monitor parou. Retome `start` ou
use `close` para tentar encerrá-las quando os dados voltarem. Não inventa preços de
saída. Quedas de energia não permitem monitorar posições.

## Dados e escopo

- Binance: descoberta dinâmica dos pares USDT mais líquidos, normalizados para BRL
  pela cotação USDTBRL, com bid/ask, volume e até 100 candles fechados em 5m, 15m,
  1h e 4h por ciclo de pesquisa. A IA recebe tendência
  EMA, RSI, ATR, suporte/resistência, volume relativo e padrões detectados — não só
  um candle atrasado nem um candle ainda em formação.
- CoinGecko: tendência como fonte de pesquisa, nunca como uma cotação executável.
- DEX Screener: descoberta de perfis e pares; sem dados de contrato, holders,
  segurança e possibilidade de venda o token fica apenas em pesquisa.
- Falhas HTTP/rate limit geram fallback/HOLD, sem repetição dentro do ciclo.

`Market.collect()` normaliza candidatos, filtra liquidez/volume/histórico, enriquece
gráficos e limita o contexto de decisão aos 12 melhores ativos elegíveis. MCP,
Telegram, GoPlus e Solscan ainda são extensões futuras: enquanto GoPlus/Solscan não
produzirem evidência de segurança, tokens on-chain não passam para paper execution.
Endpoints usados são públicos; chave CoinGecko opcional
via variável de ambiente `COINGECKO_DEMO_KEY`. `.env` não é carregado automaticamente.

## Regras e contabilidade

R$200 iniciais, 5% de margem por posição, três posições, stop obrigatório.
Bloqueia entradas com perda de 3% diária ou 8% desde o início do experimento (não
renova por semana). Saídas continuam permitidas. Preços expiram em 180 segundos.
Não reforça posições existentes. O broker aceita somente cotações recentes marcadas
como elegíveis pelo pipeline; fontes de pesquisa não podem criar uma ordem por si só.

O paper simula contratos direcionais: long lucra quando o preço sobe e short quando
cai. `amount_brl` é a margem e a IA pode propor qualquer alavancagem >= 1, sem teto
fixo nesta fase de teste. O motor ainda recusa margem insuficiente, stop que não
protege antes da liquidação estimada, preço antigo e dados/evidências ausentes.
Paper estima taxa de 0,1% sobre o notional e slippage adverso de 0,2% por lado, além do spread.
Hipóteses de simulação, não taxas garantidas. Mínimos de ordem, profundidade,
fills parciais e impostos não são modelados. Fill paper não comprova execução real.

Regras efetivas: `Policy` em `src/crypto_harness/core.py`. Hash registrado em cada
decisão. Altere somente com o processo parado. `config/` local não é carregado.
Codex recebe regras e evidências; não recebe credenciais. Usa sandbox somente
leitura, sem shell, busca web ou MCPs herdados.

## Registros e testes

`data/harness.db` mantém carteira, decisões, evidências, valorações e uso de IA.
`data/report.json` mostra saldo, posições, PnL realizado por venda, decisões e última
valoração conhecida. Execuções de teste podem ser separadas:

```powershell
.\.venv\Scripts\python.exe -m crypto_harness --data-dir data/ensaio once --observe
.\.venv\Scripts\python.exe -m pytest -q
```

Testes usam fixtures identificadas sem APIs/créditos. Lock impede duas instâncias
na mesma carteira. SQLite grava saldo e ordem juntos; restart não duplica decisão.
Não versionar `.env`, `AGENTS.md`, `docs/`, `config/`, `data/`, `logs/`, chaves ou sessões.

Integrações de análise: `ta-patterns` (MIT) é dependência direta e fornece padrões
avaliados sem olhar candles futuros. `crypto-chart-patterns` (MIT) foi auditado como
referência de padrões multi-timeframe; seu código não foi copiado. `market-analyzer`
permanece uma possível evolução futura de MCP local, não uma dependência do núcleo.

Documentação oficial: https://developers.openai.com/codex/noninteractive,
https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints,
https://docs.coingecko.com/reference/simple-price,
https://docs.dexscreener.com/api/reference.
