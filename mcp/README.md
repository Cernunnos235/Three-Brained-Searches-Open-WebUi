# Three Brained Searches — MCP Server

Portage MCP (Model Context Protocol) de l'outil Open WebUI **"Three Brained Searches Xng OR" v0.9.3** (par Lug / Cernunnos235).

Même logique que l'original :

1. **three_smart_searches** : 3 recherches SearXNG en parallèle (fallbacks DDGS puis LangSearch), LLM 1 sélectionne les URLs les plus pertinentes, scraping (Jina pour la requête 1, BeautifulSoup pour les requêtes 2 et 3, Jina pour les PDF), puis LLM 2 extrait et résume uniquement les informations pertinentes.
2. **scrape_urls** : scrape des URLs avec Jina, sans analyse LLM.
3. **summarize_urls** : scrape des URLs avec Jina puis résumé par LLM 2.

## Installation

```bash
cd mcp
pip install -r requirements.txt
```

## Configuration

Les 36 paramètres ("Valves") de l'outil original sont configurables via variables d'environnement : nom de la valve en MAJUSCULES, préfixé par `TBS_`.

Exemples :

| Variable | Valve | Rôle |
|---|---|---|
| `TBS_SEARXNG_URL` | `searxng_url` | URL de votre serveur SearXNG (défaut : `http://host.docker.internal:8080`) |
| `TBS_LLM_1_API_KEY` | `llm_1_api_key` | Clé API OpenRouter pour LLM 1 (**requis**) |
| `TBS_LLM_2_API_KEY` | `llm_2_api_key` | Clé API OpenRouter pour LLM 2 (**requis**) |
| `TBS_LLM_1_MODEL` | `llm_1_model` | Modèle LLM 1 (défaut : `mistralai/ministral-8b-2512`) |
| `TBS_LLM_2_MODEL` | `llm_2_model` | Modèle LLM 2 (défaut : `amazon/nova-2-lite-v1:free`) |
| `TBS_JINA_API_KEY` | `JINA_API_KEY` | Clé Jina optionnelle (5 URLs en parallèle au lieu de 2) |
| `TBS_LANGSEARCH_API_KEY` | `langsearch_api_key` | Clé LangSearch optionnelle (fallback) |

La liste complète des valves est visible dans `tool_code.py` (classe `Tools.Valves`). Les booléens s'écrivent `true`/`1`, les entiers et flottants en clair.

## Utilisation (stdio)

### Claude Desktop

`claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "three-brained-searches": {
      "command": "python",
      "args": ["/chemin/vers/mcp/server.py"],
      "env": {
        "TBS_SEARXNG_URL": "http://localhost:8080",
        "TBS_LLM_1_API_KEY": "sk-or-...",
        "TBS_LLM_2_API_KEY": "sk-or-..."
      }
    }
  }
}
```

### Test rapide

```bash
python -c "
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test():
    async with stdio_client(StdioServerParameters(command='python', args=['server.py'])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])

asyncio.run(test())
"
```

## Outils exposés

- **three_smart_searches** (`query_1`/`query_2`/`query_3`, `long_query`, limites d'URLs, langues, engines, catégories, freshness par requête) — renvoie un JSON avec les détails de scraping et l'analyse combinée de LLM 2.
- **scrape_urls** (`urls`) — renvoie le contenu brut des pages.
- **summarize_urls** (`urls`, `long_query`) — renvoie le résumé LLM 2.

## Prérequis

- Un serveur SearXNG local (comme l'outil original). Sans SearXNG, les fallbacks DDGS (gratuit) et LangSearch sont utilisés.
- Une clé OpenRouter pour LLM 1 et LLM 2 (~0.0006 $ par appel d'outil selon l'auteur).

## Licence

MIT (comme l'outil original). Scraper partiellement issu de "Web search v0.2.1" par siwadon.
