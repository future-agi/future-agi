import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import {
  useEnvironmentSources,
  usePrepareUploadSource,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import { useEnvironmentVersions } from "src/api/simulate-environments/versions";
import { nextVersion } from "../drafts/reviewView";
import AttachmentsCard, { ProposalsOutcome } from "./AttachmentsCard";
import { CHAT_FILES_COPY as COPY, spliceTurns } from "./chatFiles";
import { Proposals } from "./ProposalsDrawer";
import { readProgress } from "./proposals";
import { overUploadLimit } from "./sources.constants";

let nextId = 0;

/**
 * The builder chat with files: `send(text, files)` sends the text to the
 * builder as before and the files to the sources upload, and `turns` adds a
 * card per upload (each file's state, then the proposals to add or reject)
 * after the turns that were there when it was sent. Kept for this visit only.
 */
export default function useChatFiles(env, chat, { onReviewChanges } = {}) {
  const envId = env?.id;
  const [entries, setEntries] = useState([]);
  const [, setParams] = useSearchParams();
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const { data: versions } = useEnvironmentVersions(envId, {
    enabled: Boolean(env?.versioning?.enabled),
  });
  const prepare = usePrepareUploadSource(envId);
  const chatTurns = chat.turns || [];

  const update = (id, fields) =>
    setEntries((list) =>
      list.map((entry) => (entry.id === id ? { ...entry, ...fields } : entry)),
    );

  const sendFiles = (text, files, at) => {
    const limit = overUploadLimit(files, capabilities?.upload);
    if (limit) {
      enqueueSnackbar(limit, { variant: "error" });
      return;
    }
    nextId += 1;
    const id = `chat-files-${nextId}`;
    setEntries((list) => [
      ...list,
      { id, envId, at, text, files, prepared: null },
    ]);
    prepare
      .mutateAsync({
        files,
        versionId: capabilities?.environment_version_id,
        draft: capabilities?.draft,
      })
      .then(
        (prepared) => update(id, { prepared }),
        () => update(id, { failed: true }),
      );
  };

  // With files, the text shows in the files' bubble instead of its own.
  const send = (text, files = []) => {
    if (text) chat.send(text);
    if (files.length) sendFiles(text, files, chatTurns.length);
  };

  // World & tools, with the file open at `at` when a source is given.
  const openSource = (sourceId, at) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        ["source", "sourceTab", "at"].forEach((key) => next.delete(key));
        next.set("tab", "world");
        if (sourceId) next.set("source", sourceId);
        if (at) next.set("at", at);
        return next;
      },
      { replace: true },
    );

  const nextLabel = nextVersion(versions?.defaults?.environment);
  const live = summary?.sources || [];

  const turnsOf = (entry) => {
    const { id, prepared } = entry;
    const steps = [
      {
        id: `${id}-files`,
        kind: "node",
        node: (
          <AttachmentsCard
            envId={envId}
            files={entry.files}
            prepared={prepared}
            failed={entry.failed}
          />
        ),
      },
    ];
    // Added once every file is read, so the chat follows it down.
    if (prepared && !readProgress(prepared, live).reading.length) {
      if (!prepared.ui.items.length) {
        steps.push({
          id: `${id}-none`,
          kind: "note",
          text: prepared.ui.sources.length
            ? COPY.nothingToAdd
            : COPY.nothingNew,
        });
      } else {
        steps.push(
          { id: `${id}-say`, kind: "note", text: COPY.propose(entry.text) },
          {
            id: `${id}-proposals`,
            kind: "node",
            node:
              entry.outcome === undefined ? (
                <Proposals
                  envId={envId}
                  prepared={prepared}
                  layout="card"
                  nextLabel={nextLabel}
                  onDone={(outcome) => update(id, { outcome })}
                  onOpenSource={openSource}
                  onAddFiles={(files) => sendFiles("", files, chatTurns.length)}
                  onReviewChanges={onReviewChanges}
                />
              ) : (
                <ProposalsOutcome
                  outcome={entry.outcome}
                  onOpenSources={() => openSource()}
                  onReviewChanges={onReviewChanges}
                />
              ),
          },
        );
      }
    }
    return [
      {
        id: `${id}-user`,
        role: "user",
        text: COPY.attached(
          entry.files.map((file) => file.name),
          entry.text,
        ),
      },
      { id: `${id}-reply`, role: "builder", steps },
    ];
  };

  // shortcut: placed by the chat's turn count at send time, and the chat's own
  // bubble for the text is found by its text; anchor on message ids if the
  // conversation API ever carries files.
  const turns = spliceTurns(
    chatTurns,
    entries
      .filter((entry) => entry.envId === envId)
      .map((entry) => ({
        at: entry.at,
        text: entry.text,
        turns: turnsOf(entry),
      })),
  );

  return { turns, send };
}
