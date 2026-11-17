# Support triage: an Omega sample project

A workflow that reads a support message and answers with the team that should handle it and a priority. It decides from
the words the message uses, so it needs no model key and no network, and it deploys as it is.

## What is in it

- `omega.studio.json`: the project manifest. It lists one entry, `triage`, and the module and export that hold it.
- `entries/triage.ts`: the workflow: the input, the `triage_message` tool, the output.

The deployment runtime provides `@omega/core`, so the project has no `package.json`.

## Deploy it

In Future AGI, open Deployments, then New deployment. Either:

- choose **Bundle file** and pick this `.tar.gz` as you downloaded it; or
- extract it into a folder (`mkdir support-triage && tar -xzf omega-sample-support-triage.tar.gz -C support-triage`),
  then choose **Project folder** and pick that folder.

Give it a name, deploy, and keep the access token the page shows once.

## Call it

Its page shows the deployment URL. If the page says only the machine that runs Omega can reach that address, run the
call on that machine; from anywhere else it gets no answer. With the URL and the token:

```sh
curl -s -X POST "$DEPLOYMENT_URL/studio/v1/runs" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entry": "triage", "input": {"message": "I was charged twice. Please refund me, it is urgent."}}'
```

The run's output is the `triage_message` tool's result. Its `content` holds the answer: `team` is `billing`, `priority`
is `high`, and `matched` lists the words that decided it (`refund`, `charged`, `urgent`). Without the token the call
answers 401.

## Make it yours

- Change the word lists in `entries/triage.ts`.
- Replace the tool step with an agent step when a model should decide, and add the model's key to the deployment as a
  secret.
- Add entries to `omega.studio.json`: each names a module and the export that holds its workflow.
