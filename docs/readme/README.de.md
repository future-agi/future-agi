<div align="center">

<a href="https://futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=logo">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../../.github/assets/logo-dark.svg">
    <img alt="Future AGI" src="../../.github/assets/logo-light.svg" width="300">
  </picture>
</a>

# Die Infrastruktur für selbstverbessernde KI-Agenten

Future AGI nutzt Traces, Auswertungen und Simulationen eines Agenten, um ihn zu verbessern. Die Software ist Open Source und lässt sich selbst hosten.

<p>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-4b5563?style=flat-square" alt="Apache-2.0-Lizenz"></a>
  <a href="https://github.com/future-agi/future-agi/releases/latest"><img src="https://img.shields.io/github/v/release/future-agi/future-agi?style=flat-square&label=release&color=4b5563" alt="Neueste Version"></a>
  <a href="https://hub.docker.com/r/futureagi/standalone"><img src="https://img.shields.io/docker/pulls/futureagi/standalone?style=flat-square&logo=docker&logoColor=white&label=docker%20pulls&color=4b5563" alt="Docker-Pulls"></a>
  <a href="https://pypi.org/project/fi-instrumentation-otel/"><img src="https://img.shields.io/pypi/dm/fi-instrumentation-otel?style=flat-square&logo=python&logoColor=white&label=pypi&color=4b5563" alt="Downloads von fi-instrumentation-otel auf PyPI"></a>
  <a href="https://www.npmjs.com/package/@traceai/fi-core"><img src="https://img.shields.io/npm/dm/@traceai/fi-core?style=flat-square&logo=npm&logoColor=white&label=npm&color=4b5563" alt="Downloads von @traceai/fi-core auf npm"></a>
  <br>
  <a href="https://discord.com/invite/cBnWkd7T9v"><img src="https://img.shields.io/badge/discord-join-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Auf Discord chatten"></a>
  <a href="https://x.com/FutureAGI_"><img src="https://img.shields.io/badge/follow-%40FutureAGI__-000000?style=flat-square&logo=x&logoColor=white" alt="Auf X folgen"></a>
  <a href="https://www.linkedin.com/company/futureagi/"><img src="https://img.shields.io/badge/follow-LinkedIn-0a66c2?style=flat-square&logo=linkedin&logoColor=white" alt="Auf LinkedIn folgen"></a>
  <a href="https://github.com/future-agi/future-agi/pulse"><img src="https://img.shields.io/github/commit-activity/m/future-agi/future-agi?style=flat-square&label=commits&color=4b5563" alt="Commits im letzten Monat"></a>
  <a href="https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aclosed"><img src="https://img.shields.io/github/issues-search/future-agi/future-agi?query=is%3Aissue%20is%3Aclosed&style=flat-square&label=issues%20closed&color=4b5563" alt="Geschlossene Issues"></a>
</p>

<p>
  <a href="https://app.futureagi.com/auth/jwt/register?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=header_cta_cloud"><b>Kostenlos in der Cloud starten</b></a> ·
  <a href="#-quickstart"><b>Selbst hosten</b></a> ·
  <a href="https://docs.futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=header_docs"><b>Dokumentation</b></a> ·
  <a href="https://discord.com/invite/cBnWkd7T9v"><b>Discord</b></a>
</p>

<p>
  <b>SDKs:</b>
  <a href="https://github.com/future-agi/traceAI#python">Python</a> ·
  <a href="https://github.com/future-agi/traceAI#typescript--javascript">TypeScript</a> ·
  <a href="https://github.com/future-agi/traceAI#java">Java</a> ·
  <a href="https://github.com/future-agi/traceAI#go">Go</a> ·
  <a href="https://github.com/future-agi/traceAI#c">C#</a> ·
  <a href="https://github.com/future-agi/futureagi-sdk">Platform SDK</a> ·
  <a href="https://github.com/future-agi/agent-command-center-sdk">Gateway SDK</a>
</p>

<p>
  <sub>
    <a href="../../README.md">English</a> ·
    <a href="README.zh-CN.md">简体中文</a> ·
    <a href="README.ja.md">日本語</a> ·
    <a href="README.ko.md">한국어</a> ·
    <a href="README.es.md">Español</a> ·
    <a href="README.pt-BR.md">Português</a> ·
    <a href="README.fr.md">Français</a> ·
    <b>Deutsch</b>
  </sub>
</p>

<a href="https://www.youtube.com/watch?v=6keOTAOUUWI"><img alt="Demo von Future AGI auf YouTube ansehen" src="../../frontend/public/assets/readme/demo-6keOTAOUUWI-v4.png" width="100%"></a>

</div>

## So funktioniert es

Ein besseres Modell hilft jedem Agenten. Aber ein Modell allein weiß nicht, welche Antworten letzte Woche falsch waren. Es kann eine Korrektur nicht testen, bevor sie deine Nutzer erreicht, und erkennt nicht, ob sie an anderer Stelle zu Fehlern führt. Future AGI übernimmt das für dich – in einem Zyklus rund um deinen Agenten.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../.github/assets/readme/system-dark.png">
  <img alt="So verbessert sich dein Agent von v1 zu v2: Die Aufzeichnung erfasst jeden echten Lauf mit Nutzern im Produktivbetrieb Schritt für Schritt. Die Auswertung bewertet jeden Lauf und begründet, warum er bestanden hat oder fehlgeschlagen ist. Die Simulation stellt die Fehler mit simulierten Nutzern in einer Testwelt nach. Zur Verbesserung testet Epoch Korrekturen in vollständigen Läufen und behält die beste. Korrekturen, die einen zuvor bestandenen Fall verschlechtern oder nicht helfen, werden verworfen. Bei der Freigabe genehmigst du die beste Änderung, und das Gateway schützt den Produktivbetrieb. Mit neuen Läufen beginnt die nächste Runde." src="../../.github/assets/readme/system-light.png" width="100%">
</picture>

1. **[Verfolge](#trace-and-monitor)** jeden Lauf deines Agenten Schritt für Schritt.<br><sub>Tracing · Sitzungen und Nutzer · Live-Evals · Instant eval · Dashboards · Benachrichtigungen · Error Feed · Insights</sub>
2. **[Bewerte](#evaluate-and-label)** jeden Lauf und sieh, warum er bestanden hat oder fehlgeschlagen ist.<br><sub>Agenten-, LLM- und Code-Evals · Jev · Kombinierte Evals · Datensätze · Experimente · Annotationen · Evals in der CI</sub>
3. **[Simuliere](#simulate-and-test)** realistische Nutzer, die mit deinem Agenten in einer eigens für ihn erstellten Testwelt interagieren.<br><sub>Umgebungen · Szenarien und Personas · Text und Sprache · Szenarien aus dem Produktivbetrieb · Fehleranalyse</sub>
4. **[Verbessere](#improve)** den Agenten: Finde eine Änderung, die die Fehler behebt, und prüfe ihre Wirkung vor der Freigabe.<br><sub>Epoch · Prompt-Optimierung</sub>
5. **[Schütze](#gateway-and-guardrails)** den Live-Traffic mit einem Gateway und Guardrails. Neue Läufe werden aufgezeichnet, und der Zyklus beginnt von vorn.<br><sub>Eine API für alle Modelle · Routing und Fallbacks · Guardrails · Caching · Schlüssel und Budgets · Logs und Analysen · Schattenexperimente · MCP und A2A</sub>

Ergänzend zu diesem Zyklus stehen dir unter **[Entwickeln](#build)** Prompts, Agent Playground, Wissensdatenbanken, Falcon AI, ein MCP-Server und Team-Arbeitsbereiche zur Verfügung. Du kannst mit jedem Baustein anfangen. Alle verwenden dieselben Traces, Datensätze und Evals. Weitere Bausteine kannst du bei Bedarf ergänzen.

<a id="-quickstart"></a>
<a id="-quickstart-60-seconds"></a>

<a id="get-started"></a>

## Erste Schritte

**1. Erstelle deine Schlüssel.** [Lege ein kostenloses Cloud-Konto an](https://app.futureagi.com/auth/jwt/register?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_cloud) oder starte Future AGI lokal mit Docker (Compose ab v2.24, 2 vCPUs und 4 GB für Docker):

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/install          # Windows (PowerShell): .\bin\install.ps1
```

Das Installationsskript schreibt die Secrets dieser Installation in `.env`, lädt die Images herunter (etwa 800 MB) und wartet, bis alle Dienste laufen. Der erste Start dauert einige Minuten. Öffne dann <http://localhost:3000> und erstelle dein Konto.

Öffne in beiden Fällen **Keys** in der Seitenleiste, wähle **Add API Key** und kopiere den API-Schlüssel und den geheimen Schlüssel, bevor du den Dialog schließt.

**2. Installiere das SDK und setze deine Schlüssel.**

```bash
pip install -U fi-instrumentation-otel traceai-openai openai
export FI_API_KEY="<your API key>" FI_SECRET_KEY="<your secret key>" FI_PROJECT_NAME="my-agent"
export OPENAI_API_KEY="<your OpenAI key>"
# Nur beim Selbsthosting: Traces an den Collector deiner Installation senden
export FI_BASE_URL="http://localhost:4318"
```

**3. Starte deine App mit automatischem Tracing.** Dein Code braucht keine Tracing-Imports. Starte die App mit `fi-instrument`. Für jede verwendete Bibliothek wird Tracing aktiviert, wenn ihr traceAI-Paket installiert ist:

```python
# app.py: reiner OpenAI-Code ohne Ergänzungen
from openai import OpenAI

response = OpenAI().chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Plan a three-step onboarding email."}],
)
print(response.choices[0].message.content)
```

```bash
fi-instrument python app.py      # funktioniert auch mit uvicorn, celery und anderen Befehlen
```

Öffne **Tracing** in der Seitenleiste und wähle `my-agent`. Dort siehst du den Aufruf mit Eingabe, Ausgabe, Tokenanzahl und Zeitmessung. Als Nächstes kannst du [den Aufruf mit einem Eval bewerten](https://docs.futureagi.com/docs/evaluation?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_next) oder [eine Testumgebung](https://docs.futureagi.com/docs/environments?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=quickstart_next) für deinen Agenten erstellen.

<details>
<summary><b>Lieber eine Zeile im Code?</b></summary>

```python
import fi_instrumentation

fi_instrumentation.init(project_name="my-agent")
# ...dein App-Code; ab hier ist Tracing für installierte traceAI-Integrationen aktiv
```

Du kannst auch deinen Startbefehl unverändert lassen und `FI_AUTO_INSTRUMENT=true` setzen. Die meisten Integrationen aktivieren sich automatisch. Einige, etwa Strands und Temporal, brauchen einen Aufruf zur Initialisierung. Die vollständige Liste, Datenschutzeinstellungen und Hinweise zu `fi-instrument doctor` findest du unter [Tracing ohne Codeänderungen für Python](https://github.com/future-agi/traceAI/blob/main/docs/python/auto-instrumentation.md).

</details>

<details>
<summary><b>TypeScript</b></summary>

```bash
npm install "@traceai/fi-core@^1.4.0" @traceai/openai openai
```

Speichere den folgenden Code als `trace.mts` und führe ihn mit Node.js ab Version 22.23.4 über `node trace.mts` aus:

```typescript
import OpenAI from "openai";
import { register, ProjectType } from "@traceai/fi-core";
import { OpenAIInstrumentation } from "@traceai/openai";

const provider = register({ projectName: "my-agent", projectType: ProjectType.OBSERVE });
const instrumentation = new OpenAIInstrumentation();
instrumentation.setTracerProvider(provider);
instrumentation.manuallyInstrument(OpenAI);

const response = await new OpenAI().chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Plan a three-step onboarding email." }],
});
console.log(response.choices[0].message.content);
await provider.shutdown(); // den Trace senden, bevor das Skript endet
```

</details>

## Funktionen im Überblick

<a id="trace-and-monitor"></a>

### Aufzeichnen und überwachen

Sieh, was dein Agent getan hat, was der Lauf gekostet hat und wo Fehler aufgetreten sind.

- **[Tracing](https://docs.futureagi.com/docs/observe?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_tracing)**: Jeder LLM-Aufruf, Tool-Aufruf und Agentenschritt wird mit Eingaben, Ausgaben, Tokenanzahl, Kosten und Latenz erfasst. Ein Graph zeigt den Ablauf. Das Tracing basiert auf OpenTelemetry, bietet fertige Instrumentierung für über 50 Frameworks und erfasst auch Sprachanrufe samt Aufzeichnungen.
- **[Sitzungen und Nutzer](https://docs.futureagi.com/docs/observe/features/users?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_users)**: Gruppiere Traces zu Unterhaltungen und sieh dir alle Traces und Metriken für einen einzelnen Endnutzer an.
- **[Evals für Live-Traffic](https://docs.futureagi.com/docs/observe/features/evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_live_evals)**: Bewerte eingehende Produktions-Traces automatisch – für jeden Span oder für eine Stichprobe.
- **[Instant eval](https://docs.futureagi.com/docs/observe/features/instant-eval?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_instant_eval)**\*: Stelle Jev in jeder Ansicht eine Ja/Nein-Frage oder eine Frage zur Einordnung der Traces, Sitzungen oder Nutzer, etwa „Hat der Assistent das Anliegen gelöst?“. Dafür musst du nicht erst ein Eval erstellen.
- **[Dashboards](https://docs.futureagi.com/docs/observe/features/dashboard?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_dashboards)**: Erstelle und teile Diagramme zu Fehlerrate, Latenz, Tokenverbrauch, Kosten und Eval-Ergebnissen.
- **[Alerts](https://docs.futureagi.com/docs/observe/features/alerts?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_alerts)**: Erhalte eine E-Mail oder Slack-Nachricht, wenn eine Metrik oder ein Eval-Ergebnis einen Schwellenwert über- oder unterschreitet.
- **[Error Feed](https://docs.futureagi.com/docs/error-feed?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_error_feed)**\*: Fasst fehlerhafte Traces nach Problem zusammen, erklärt die wahrscheinliche Ursache und schlägt eine Korrektur vor.
- **[Insights](https://docs.futureagi.com/docs/observe/features/insights?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_insights)**\*: Berichte ordnen Traces, Sitzungen oder Nutzer in Kategorien ein, messen deren Häufigkeit und erklären die Ergebnisse anhand verlinkter Beispiele.

<img alt="Animation: Der Trace eines LangGraph-Agenten zur Fahrtbuchung wird geöffnet. Die Ansicht zoomt in den Schrittbaum und dann in den Modellaufruf. Das Tool meldet, dass der Fahrer unterwegs ist, und der Agent gibt diese Information korrekt an den Fahrgast weiter." src="../../.github/assets/readme/trace.gif" width="100%">

<a id="evaluate-and-label"></a>

### Bewerten und kennzeichnen

Prüfe für jede Antwort, ob sie richtig war und warum. Ein Eval kann so wenig Aufwand wie ein regulärer Ausdruck erfordern oder so gründlich sein wie ein Agent, der jede Aussage überprüft.

| Eval-Typ | So fällt die Entscheidung | Geeignet, wenn |
|---|---|---|
| **Agent als Prüfer** | Ein Prüfagent untersucht den Lauf in mehreren Schritten, schlägt Informationen nach und gibt ein begründetes Urteil ab. | Die Prüfung erfordert Nachforschungen, etwa bei einem langen Agentenlauf mit vielen Schritten oder bei Aussagen, die belegt werden müssen. |
| **LLM als Prüfer** | Ein Modell prüft die Ausgabe in einem Durchgang anhand deiner Kriterien und begründet sein Urteil. | Die Qualität lässt sich in Worten beschreiben: Quellentreue, Ton, Sicherheit, Einhaltung von Anweisungen oder eigene Regeln. |
| **Code** | Dein Python- oder JavaScript-Code läuft in einer Sandbox. Gleiche Eingabe, gleiches Ergebnis, kein Modellaufruf. | Das Ergebnis lässt sich berechnen, etwa mit einer JSON-Prüfung, einem regulären Ausdruck, BLEU oder ROUGE, einem Ähnlichkeitsmaß oder deiner Geschäftslogik. |

Der **Agent als Prüfer** ist für Agenten ausgelegt. Er prüft einen Lauf so, wie es ein sorgfältiger Mensch tun würde:

- **Keine Variablenzuordnung.** Formuliere deine Regel in einfachen Worten und setze `{{trace}}`, `{{span}}`, `{{session}}`, `{{row}}` oder `{{call}}` ein. Der Prüfer erhält das gesamte Objekt. Du musst keine Spalten zuordnen.
- **Er liest den gesamten Lauf.** Statt nur eine Eingabe und eine Ausgabe zu sehen, geht der Prüfer den Trace-Baum Span für Span durch, um den fehlerhaften Schritt zu finden.
- **Er prüft Fakten während der Bewertung.** Aktiviere [die Websuche, deine Wissensdatenbanken oder eigene Tools über MCP](https://docs.futureagi.com/docs/evaluation/guides/advanced-usage?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_agent_tools), damit der Prüfer Aussagen mit deiner Dokumentation, deiner Datenbank oder Quellen im Web abgleicht.
- **[Bestimme, wie viel Kontext er erhält](https://docs.futureagi.com/docs/evaluation/guides/advanced-usage?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_data_injection)**: die zugeordneten Felder, die Datensatzzeile, den Span, den gesamten Trace, die gesamte Sitzung oder das Transkript und die Aufzeichnung eines Anrufs.
- **Auch lange Läufe passen ins Kontextfenster.** Sehr lange Traces und Unterhaltungen werden dafür verdichtet. Bilder und Audio bleiben unverändert.
- **Wähle die Prüftiefe.** *Quick* antwortet in einem Durchgang, *Agent* untersucht den Fall in mehreren Schritten und *Auto* wägt beides ab. Wähle eine kurze, ausführliche oder individuell gestaltete Begründung.
- Nutze deinen Schlüssel für OpenAI, Anthropic, Bedrock oder Vertex AI oder die von Future AGI verwalteten Modelle.

- **[Über 130 integrierte Evals](https://docs.futureagi.com/docs/evaluation/builtin?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_builtin_evals)**, unter anderem für Halluzinationen, Belegbarkeit, Tool-Nutzung, Aufgabenerfüllung, personenbezogene Daten (PII), toxische Inhalte, Ton und gültiges JSON. Alternativ kannst du [eigene Evals schreiben](https://docs.futureagi.com/docs/evaluation/guides/custom-evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_custom_evals), in natürlicher Sprache oder als Code.
- **[Prüfmodell wählen](https://docs.futureagi.com/docs/evaluation/concepts/evaluator-models?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_judges)**: Jev, das Entscheidungsmodell von TypeSafe, liefert über deinen TypeSafe- oder OpenRouter-Schlüssel typisierte Urteile mit Wahrscheinlichkeiten: bestanden/nicht bestanden, eine Kategorie oder einen Wert auf deiner Bewertungsskala. Alternativen sind die Turing models\* von Future AGI für Text, Bild und Audio oder ein eigenes Modell von OpenAI, Bedrock, Vertex AI, Azure oder einem beliebigen Endpunkt.
- **[Kombinierte Evals](https://docs.futureagi.com/docs/evaluation/concepts/composite-evals?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_composite)**: Fasse mehrere Evals gewichtet zu einem Ergebnis mit einem Schwellenwert zusammen, zum Beispiel quellentreu *und* im richtigen Ton *und* vollständig.
- **[Fehlerlokalisierung](https://docs.futureagi.com/docs/evaluation/concepts/error-localization?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_error_localization)**: Schlägt ein Eval fehl, zeigt es dir, welche Eingabe den Fehler verursacht hat.
- **[Feedback](https://docs.futureagi.com/docs/evaluation/concepts/feedback?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_feedback)**: Korrigiere ein Ergebnis und lass es erneut bewerten, damit die Evals zunehmend dem entsprechen, was dein Team als richtig ansieht.
- **Evals überall ausführen**: für Datensätze, Live-Traces und jeden simulierten Anruf, [in der CI bei jedem Pull Request](https://docs.futureagi.com/docs/evaluation/guides/cicd?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_cicd) oder als Guardrails im Gateway.
- **[Datensätze](https://docs.futureagi.com/docs/dataset?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_datasets)**: Tabellen mit Testfällen aus Dateien, Produktions-Traces, Hugging Face, dem SDK oder generierten Daten. Prompts und Evals verarbeiten die Zeilen und schreiben ihre Ergebnisse in neue Spalten.
- **[Experimente](https://docs.futureagi.com/docs/dataset/guides/run-an-experiment?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_experiments)**: Teste mehrere Kombinationen aus Prompts, Agenten und Modellen mit demselben Datensatz, vergleiche sie direkt und wähle die beste aus.
- **[Annotationen](https://docs.futureagi.com/docs/annotations?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_annotations)**: Warteschlangen für die manuelle Prüfung von Ausgaben. Du kannst Labels vergeben, Aufgaben zuweisen, die Übereinstimmung zwischen Prüfern messen und die Ergebnisse wieder in einen Datensatz exportieren.

<img alt="Animation: Eine Eval-Regel in natürlicher Sprache wird an einem Transkript getestet. Der Agent meldet nach einem fehlgeschlagenen Tool-Aufruf fälschlich Erfolg. Der LLM-Prüfer bewertet den Test als nicht bestanden und begründet dies schriftlich. Bei derselben Prüfung gibt Jev Fail mit einer Wahrscheinlichkeit von 0,04 zurück." src="../../.github/assets/readme/evals.gif" width="100%">

<a id="simulate-and-test"></a>

### Simulieren und testen

Teste jede Änderung mit realistischen simulierten Nutzern, bevor echte Nutzer sie sehen. Ein Transkript kann nach einem Erfolg klingen, obwohl der Agent einen Fehler gemacht hat. Deshalb prüft jeder Testanruf, was sich tatsächlich geändert hat.

1. **[Verbinde deinen Agenten](https://docs.futureagi.com/docs/environments/guides/build-an-environment?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_connect)**: Nutze ein Code-Repository, einen Upload oder einen Agenten, der auf Vapi, Retell AI oder LiveKit läuft oder über eine Telefonnummer erreichbar ist.
2. **Das System analysiert deinen Agenten** und hält fest, wozu er dient, welche Tools er aufruft, welche Regeln er einhalten muss und welche Aufgaben er übernimmt. Du kannst Fragen stellen oder in natürlicher Sprache Vorgaben machen.
3. **Es erstellt eine Testwelt für deinen Agenten**: Alles, worauf seine Tools zugreifen, wird als Testkopie mit den für das Szenario nötigen Daten angelegt. Ein Tool-Aufruf im Test ändert also tatsächlich etwas – aber nie deine Produktivdaten.
4. **[Simulierte Nutzer spielen die Szenarien durch](https://docs.futureagi.com/docs/environments/concepts/scenarios-and-trials?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_scenarios)**, per Text oder Sprache. Jeder hat eine Persona mit Namen, Alter, Persönlichkeit, Sprache und Akzent. Führe jedes Szenario mehrmals aus, um auch sporadische Fehler zu finden.
5. **[Jeder Anruf wird bewertet](https://docs.futureagi.com/docs/environments/guides/read-run-results?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_results)**: Für jedes Ziel zählt der tatsächliche Zustand der Testwelt. Hinzu kommen deine eigenen Evals. Sagt ein Agent „Deine Buchung ist storniert“, obwohl sie noch aktiv ist, besteht er den Test nicht.
6. **[Debug failures](https://docs.futureagi.com/docs/environments/guides/debug-failures?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_debug)** analysiert den gesamten Lauf und erklärt für jedes Ziel, was schiefgelaufen ist. Von dort startest du [Epoch](#improve) mit einem Klick.

Das kann die Testwelt enthalten:

- **Tools jeder Art.** Integrierte Funktionen, HTTP-APIs und MCP-Server antworten aus der Testwelt. So verhält sich der Agent wie im Produktivbetrieb.
- **Die Apps, mit denen dein Agent arbeitet**, etwa ein Postfach, ein Slack-Arbeitsbereich oder ein Kalender, jeweils mit realistischen Inhalten simuliert.
- **Die benötigten Datenbanken.** Erwartet dein Agentencode Postgres, MySQL, ClickHouse, MongoDB, Redis, Qdrant, RabbitMQ, NATS oder MinIO, stellt der Builder den jeweiligen Dienst bereit.
- **Mehrere Teilnehmer.** In einem Szenario können mehrere simulierte Personen oder Agenten an derselben Unterhaltung teilnehmen.
- **Umfang.** Bis zu 1.000 Szenarien pro Build, mehrere parallel laufende Testwelten und bis zu 20 Wiederholungen pro Szenario.

Erstelle Szenarien aus [Sitzungen, Anrufen und Transkripten aus dem Produktivbetrieb oder aus Datensatzzeilen](https://docs.futureagi.com/docs/environments/guides/import-evidence?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_import). So werden aus den Fehlern der letzten Woche Tests für jede Änderung.

<img alt="Animation: In einem Simulationslauf nennt Debug failures jedes verfehlte Ziel. Über Run Epoch startet die Suche. Anschließend stehen vier Kandidaten zur Prüfung bereit. Die Erfolgsquote bei zurückgehaltenen Testfällen ist von 0 % auf 75 % gestiegen." src="../../.github/assets/readme/simulate.gif" width="100%">

<a id="improve"></a>

### Verbessern

Finde eine Änderung, die die Fehler behebt, und prüfe ihre Wirkung vor der Freigabe.

- **[Epoch](https://docs.futureagi.com/docs/environments/guides/run-epoch?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_epoch)**\*: Epoch geht von einem fehlgeschlagenen Lauf aus und sucht eine Agentenversion, die die Fehler behebt, ohne zuvor bestandene Tests zu brechen. Du legst fest, was Epoch ändern darf (Anweisungen, Tools, Einstellungen oder Code) und welche Grenzen für Kosten und Antwortzeit gelten. Epoch prüft das Ergebnis an Fällen, die bei der Suche nicht verwendet wurden. Du prüfst den Diff und entscheidest über die Freigabe.
- **[Prompt-Optimierung](https://docs.futureagi.com/docs/optimization?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_optimization)**: Verbessere einen Prompt anhand eines Datensatzes mit GEPA, ProTeGi, PromptWizard, Bayes’scher Suche, Meta-Prompting oder Zufallssuche. Vergleiche jeden Versuch per Diff.

<a id="gateway-and-guardrails"></a>

### Gateway und Guardrails

Rufe jedes Modell über denselben Endpunkt auf und prüfe dabei jede Anfrage.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../.github/assets/readme/gateway-dark.png">
  <img alt="Deine App verbindet einen OpenAI-kompatiblen Client mit Agent Command Center. Bei jeder Anfrage prüft das Gateway Schlüssel, Budget und Ratenlimit, wendet Guardrails auf die Anfrage an, prüft den Cache und leitet die Anfrage an einen Anbieter weiter. Es prüft auch die Antwort mit Guardrails und erfasst Logs und Traces. Bei einem Timeout von OpenAI geht die Anfrage an Anthropic. Gemini, Bedrock und dein eigenes vLLM stehen ebenfalls zur Verfügung." src="../../.github/assets/readme/gateway-light.png" width="100%">
</picture>

- **[Eine API für alle Modelle](https://docs.futureagi.com/docs/command-center?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_gateway)**: Ein in Go geschriebener, OpenAI-kompatibler Endpunkt. Er unterstützt OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI und Cohere nativ sowie alle OpenAI-kompatiblen Anbieter, einschließlich selbst gehosteter Modelle.
- **[Routing und Fallbacks](https://docs.futureagi.com/docs/command-center/features/routing?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_routing)**: Routing per Round-Robin, nach Gewichtung, niedrigster Latenz, geringsten Kosten oder schnellster Antwort sowie adaptives Routing. Mit Wiederholungsversuchen, Failover und Circuit Breakern.
- **[Guardrails](https://docs.futureagi.com/docs/command-center/features/guardrails?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_guardrails)**: Integrierte Scanner prüfen Anfragen und Antworten unter anderem auf personenbezogene Daten, Prompt-Injection, offengelegte Secrets und unsichere Tool-Nutzung. Ergänze die Protect models\* von Future AGI oder binde 15 Anbieter wie Lakera, Presidio und Llama Guard ein. Regeln kannst du zuerst in einem Playground testen.
- **[Caching](https://docs.futureagi.com/docs/command-center/features/caching?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_caching)**: Exaktes und semantisches Caching im Arbeitsspeicher, in Redis oder im Objektspeicher.
- **[Schlüssel, Budgets und Ratenlimits](https://docs.futureagi.com/docs/command-center/features/rate-limiting?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_limits)**: Separate Schlüssel für jede App oder jedes Team, jeweils mit Ausgabenbudget und Ratenlimits.
- **[Logs und Analysen](https://docs.futureagi.com/docs/command-center/features/cost-tracking?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_cost)**: Jede Anfrage wird mit Kosten, Tokenanzahl, Latenz und eigenen Attributen protokolliert. Dazu kommen Analysen, Monitoring, Sitzungen und Webhooks.
- **[Schattenexperimente](https://docs.futureagi.com/docs/command-center/features/shadow-experiments?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_shadow)**: Kopiere einen Teil des Live-Traffics an ein anderes Modell und vergleiche Kosten, Latenz und Qualität, bevor du wechselst.
- **[MCP und A2A](https://docs.futureagi.com/docs/command-center/features/mcp-a2a?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_mcp)**: Leite die Tool-Aufrufe deiner Agenten und die Kommunikation zwischen Agenten über dasselbe Gateway.

<a id="build"></a>

### Entwickeln

- **[Prompts](https://docs.futureagi.com/docs/prompt?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_prompts)**: Schreibe und versioniere Prompt-Vorlagen, teste sie mit echten Daten und Modellen und beobachte sie im Produktivbetrieb.
- **[Agent Playground](https://docs.futureagi.com/docs/agent-playground?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_agent_playground)**: Erstelle auf einer Arbeitsfläche aus Prompt- und Agentenknoten einen mehrstufigen Agenten. Führe ihn aus und sieh dir das Ergebnis jedes Schritts an.
- **[Wissensdatenbanken](https://docs.futureagi.com/docs/knowledge-base?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_knowledge_base)**: Lade deine Dokumente hoch, damit Prompts und Evals darauf zugreifen können.
- **[Falcon AI](https://docs.futureagi.com/docs/falcon-ai?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_falcon)**\*: Ein Agent im Dashboard. Stelle Fragen oder Aufgaben in natürlicher Sprache, etwa „Warum ist dieser Trace fehlgeschlagen?“ oder „Erstelle einen Datensatz aus diesen Produktions-Traces“. Falcon AI führt sie aus.
- **[MCP-Server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=module_mcp_server)**: Nutze Future AGI direkt aus deinem Editor oder Coding-Agenten, etwa um fehlerhafte Traces zu finden und die Ursache zu beheben.
- **Arbeitsbereiche und Zugriff**: Jedes Team hat eigene Arbeitsbereiche, Mitglieder und Rollen. Für größere Organisationen stehen Audit-Logs und SCIM zur Verfügung.

<sub>\* Erfordert Future AGI Cloud oder bei einer selbst gehosteten Installation einen Enterprise-Lizenzschlüssel. Siehe [Open Source und deine Daten](#open-source-and-your-data).</sub>

## Kompatibilität mit deinem Stack

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../.github/assets/readme/integrations-dark.png">
  <img alt="Integrationen: Agenten-Frameworks (LangChain, LangGraph, LlamaIndex, CrewAI, OpenAI Agents SDK, Claude Agent SDK, Google ADK, PydanticAI, DSPy, Haystack, Mastra, Vercel AI SDK, smolagents, AutoGen, Instructor, LiteLLM), Modellanbieter (OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI, Mistral, Groq, OpenRouter, Ollama, vLLM, Hugging Face, xAI, DeepSeek, Together, Fireworks, Cohere), Sprache und Datenabruf (LiveKit, Pipecat, ElevenLabs, Qdrant, Milvus, MongoDB, Redis, pgvector) sowie Standards und Tools (OpenTelemetry, MCP, Agent2Agent, n8n, Guardrails AI, Elasticsearch, watsonx)." src="../../.github/assets/readme/integrations-light.png" width="100%">
</picture>

Future AGI nutzt offene Schnittstellen, sodass du dein Framework und deine Modelle beibehalten kannst: OpenTelemetry für Traces, eine OpenAI-kompatible API für das Gateway und SQL mit Postgres und ClickHouse für die Speicherung.

| Integration | Sprachen | Anbindung |
|---|---|---|
| [OpenTelemetry](https://docs.futureagi.com/docs/observe?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_otel) | Beliebig | Sende OTLP-Traces aus deiner App oder deinem Collector. |
| [Automatisches Tracing](https://github.com/future-agi/traceAI/blob/main/docs/python/auto-instrumentation.md) | Python | Starte deine App mit `fi-instrument`. Für die meisten Bibliotheken mit traceAI-Paket funktioniert das Tracing ohne Codeänderungen. Einige brauchen einen Aufruf zur Initialisierung. |
| [traceAI SDKs](https://github.com/future-agi/traceAI) | Python, TypeScript, Java, Go, C# | Instrumentierung in Python und TypeScript, Client-Wrapper in Java, Middleware in Go und Spans in C#. |
| LangChain und LangGraph | Python, TypeScript | `traceai-langchain` oder `@traceai/langchain` |
| OpenAI und OpenAI Agents SDK | Python, TypeScript | `traceai-openai`, `traceai-openai-agents` |
| Anthropic und Claude Agent SDK | Python, TypeScript | `traceai-anthropic`, `traceai-claude-agent-sdk` |
| LlamaIndex | Python, TypeScript | `traceai-llamaindex` oder `@traceai/llamaindex` |
| CrewAI, AutoGen, DSPy, PydanticAI, Google ADK | Python | Je ein traceAI-Paket |
| Vercel AI SDK, Mastra | TypeScript | `@traceai/vercel`, `@traceai/mastra` |
| [Agent Command Center](https://docs.futureagi.com/docs/command-center?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_gateway) | Jeder OpenAI-kompatible Client | Setze die Basis-URL deines Clients auf die URL des Gateways. |
| [MCP-Server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=int_mcp) | Jeder MCP-Client | Nutze Future AGI aus deinem Editor oder Coding-Agenten. |
| Vapi, Retell AI, LiveKit | Sprachagenten | Teste die Agenten mit Simulate dort, wo sie laufen. |
| LiveKit, Pipecat | Python | Zeichne Sprachpipelines mit `traceai-livekit` und `traceai-pipecat` auf. |

Ebenfalls unterstützt: Pinecone, Weaviate, Chroma, LanceDB, Agno, Strands, Semantic Kernel, BeeAI, Cerebras, Vertex AI, SageMaker und weitere in [traceAI](https://github.com/future-agi/traceAI).

<details>
<summary><b>SDKs</b></summary>

| Paket | Installation | Funktion |
|---|---|---|
| [traceAI](https://github.com/future-agi/traceAI) | `pip install fi-instrumentation-otel` · `npm i @traceai/fi-core` | OpenTelemetry-Tracing für KI-Frameworks, mit automatischem Tracing ohne Codeänderungen für Python |
| [futureagi](https://github.com/future-agi/futureagi-sdk) | `pip install futureagi` | Platform SDK: Datensätze, Prompts, Wissensdatenbanken, Evals |
| [agent-learning-kit](https://github.com/future-agi/agent-learning-kit) | `pip install agent-learning-kit` | Lokale Tests, Simulation und Optimierung für Agenten |
| [agentcc](https://github.com/future-agi/agent-command-center-sdk) | `pip install agentcc` · `npm i @agentcc/client` | Client-SDKs für das Gateway |

</details>

[Alle Integrationen →](https://docs.futureagi.com/docs/integrations?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=integrations_catalog)

## Selbst hosten

| | **Standalone** (Standard) | **Distributed** | **Kubernetes** |
|---|---|---|---|
| Installation | `./bin/install` | `./bin/install --distributed` | [Helm-Chart](../../deploy/helm/futureagi) |
| Komponenten | Ein App-Container, Postgres, ClickHouse | Ein Container pro Dienst, PeerDB, Kafka | Distributed-Installation auf Kubernetes ab 1.27 |
| Ressourcen | 2 vCPUs, 4 GB | Mindestens 4 vCPUs, 12–16 GB | Zum Ausprobieren etwa 4 CPUs und 8 GiB |

**Wähle die Variante, bevor du Daten hinzufügst:** Es gibt keinen unterstützten Weg, die Daten einer Standalone-Installation später zu Distributed oder Helm zu migrieren ([Wechsel zwischen Varianten](../../INSTALLATION.md#switching-between-standalone-and-distributed)). Nimmt eine Installation an automatischen Updates teil, schließe den [sicheren Übergang zum manuellen Betrieb](../../deploy/update/README.md#safe-handoff-to-manual-operations) ab, bevor du die Installation stoppst oder deinstallierst.

Alle Optionen, Upgrades, Installationen ohne Netzanbindung und Hilfe bei Problemen findest du in [INSTALLATION.md](../../INSTALLATION.md), [deploy/README.md](../../deploy/README.md) und der [Anleitung zum Selbsthosting](https://docs.futureagi.com/docs/self-hosting?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=self_host).

<a id="open-source-and-your-data"></a>

## Open Source und deine Daten

Der Kern steht unter der Apache-2.0-Lizenz. Code in Verzeichnissen namens `ee/` unterliegt der [Future AGI Enterprise License](../../LICENSE-EE). Auf einer selbst gehosteten Installation benötigen Error Feed, Insights, Instant eval, Turing models, Protect models und Epoch einen Enterprise-Lizenzschlüssel, der die jeweilige Funktion aufführt. Falcon AI setzt einen Enterprise-Tarif voraus. Details stehen in der [Referenz zur Enterprise Edition](https://docs.futureagi.com/docs/self-hosting/configuration/reference#enterprise-edition). Jev-Evals funktionieren mit deinem eigenen TypeSafe- oder OpenRouter-Schlüssel in jeder Edition. Siehe auch [NOTICE](../../NOTICE).

<a id="telemetry"></a>

### Telemetrie

Selbst gehostete Installationen senden Telemetriedaten zur Bereitstellung. **Die Telemetrie ist standardmäßig aktiviert.** Bei der einmaligen Registrierung werden die **E-Mail-Adressen und E-Mail-Domains der Eigentümer-, Admin- und Mitarbeiterkonten der Installation** übertragen. Danach werden regelmäßig Nutzungszahlen gesendet. **Traces, Prompts, Completions, Datensätze und API-Schlüssel werden niemals gesendet.** Installiere zum Deaktivieren mit `./bin/install --no-telemetry` oder setze `FUTURE_AGI_TELEMETRY_DISABLED=true` in `.env` (bei Helm: `config.telemetry=false`). Auch bei deaktivierter Telemetrie sendet die Installation einmalig eine Registrierung ohne E-Mail-Adressen. Blockiere `api.futureagi.com`, wenn nichts gesendet werden soll. Unter [Telemetrie und ausgehende Verbindungen](https://docs.futureagi.com/docs/self-hosting/configuration/telemetry) findest du die genauen Inhalte der Nachrichten und alle ausgehenden Verbindungen.

## Support

Hier findest du Antworten:

- Die [Dokumentation](https://docs.futureagi.com?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_docs) beschreibt alle Funktionen mit Anleitungen und Praxisbeispielen. Änderungen an der Dokumentation kannst du auf GitHub vorschlagen.
- In den [FAQ](https://docs.futureagi.com/docs/faq?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_faq) findest du Antworten auf die häufigsten Fragen.
- Über den [MCP-Server](https://docs.futureagi.com/docs/falcon-ai/guides/use-the-mcp-server?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=support_mcp) kann dein Editor oder Coding-Agent Informationen in deinem Arbeitsbereich nachschlagen.

Supportkanäle:

- **Stelle eine Frage** in [GitHub Discussions](https://github.com/orgs/future-agi/discussions) oder auf [Discord](https://discord.com/invite/cBnWkd7T9v). Füge Code, Screenshots und Versionsangaben hinzu, damit wir schneller helfen können.
- **Melde einen Fehler** oder **schlage eine Funktion vor**. Nutze dazu die [Issue-Vorlagen](https://github.com/future-agi/future-agi/issues/new/choose).
- **Cloud-Konto oder Abrechnung:** Schreibe an [support@futureagi.com](mailto:support@futureagi.com).
- **Sicherheit:** Melde Schwachstellen vertraulich, wie in [SECURITY.md](../../SECURITY.md) beschrieben.
- **Dienststatus:** [status.futureagi.com](https://status.futureagi.com).

## Mitwirken

Beiträge sind willkommen:

- Richte deine Entwicklungsumgebung nach [CONTRIBUTING.md](../../CONTRIBUTING.md) ein und wähle dann ein Issue mit dem Label [`good first issue`](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
- Kommentiere [Issues](https://github.com/future-agi/future-agi/issues) und teile Ideen in den [Diskussionen](https://github.com/orgs/future-agi/discussions).
- Was als Nächstes ansteht, erfährst du in der [öffentlichen Roadmap](https://futureagi.com/roadmap?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=roadmap), in den [Releases](https://github.com/future-agi/future-agi/releases) und im [Changelog](../../CHANGELOG.md).

Neuigkeiten findest du auf [X](https://x.com/FutureAGI_), [LinkedIn](https://www.linkedin.com/company/futureagi/), [YouTube](https://www.youtube.com/@Future_AGI) und im [Blog](https://futureagi.com/blog?utm_source=github&utm_medium=readme&utm_campaign=oss_repo&utm_content=community).

## GitHub-Sterne im Zeitverlauf

<a href="https://star-history.com/#future-agi/future-agi&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date&theme=dark">
    <img alt="Anzahl der GitHub-Sterne für future-agi/future-agi im Zeitverlauf" src="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date" width="100%">
  </picture>
</a>

<div align="center">

Wenn Future AGI dir hilft, deinen Agenten zu verbessern, hilft ein ⭐ anderen Entwicklern, das Projekt zu finden.

</div>