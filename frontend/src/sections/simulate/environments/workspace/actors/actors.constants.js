import { ACTOR_CHANNEL_LABELS } from "src/components/VoiceDetailDrawerV2/constants";

// Every copy string on the Actors tab, so nothing is inlined in the JSX.
export const ACTORS_COPY = {
  callActors: {
    title: (n) => `Actors (${n})`,
    subtitle:
      "People on the person's side who are not the person. Change them in the builder chat.",
    channels: ACTOR_CHANNEL_LABELS,
    scenarios: (n) => `In ${n} scenario${n === 1 ? "" : "s"}`,
  },

  worldActors: {
    title: "Actors",
    groupTitle: (title, n) => `${title} (${n})`,
    intro:
      "Every simulated person in this world: what each knows, when they join and what they're allowed to do. A persona is how one of them behaves. Edits wait in the draft until you save.",
    notSet: "Not set",
    removeTitle: (name) => `Remove ${name}?`,
    removeBody: "They leave the draft. Saved versions keep them.",
    loading: "Loading the actors…",
    loadFailedTitle: "Couldn't load the actors",
    loadFailed: "Try again, or refresh the page.",
    viewFailedTitle: "Couldn't load what they see",
    saveFailed: "Couldn't save the change. Try again in a moment.",
    subtitle:
      "Everyone the agent deals with who is not the agent. Edits wait in the draft until you save.",
    unsaved: "Unsaved changes: saved with the environment's other changes",
    everyoneAbout:
      "Everyone the agent deals with who is not the agent: the caller, people with them, and people and systems in the world. Any actor can speak in the conversation, act through the world's actions, or both.",
    add: "Add an actor",
    edit: "Edit",
    editNamed: (name) => `Edit ${name}`,
    remove: "Remove",
    removeNamed: (name) => `Remove ${name}`,
    empty: "No actors yet. Add one, or ask the builder chat.",
    wants: "Wants",
    mayDo: "May do",
    nothing: "Nothing in the world",
    notInWorld: "not an action of this world",
    inUse: (name, count) =>
      `${name} is in ${count === 1 ? "1 scenario" : `${count} scenarios`}. Take them out of ${count === 1 ? "it" : "those scenarios"} first, by editing the scenario or asking the builder chat.`,
    showView: "What they see",
    hideView: "Hide what they see",
    sees: "Sees",
    neverSees: "Never sees",
    comesIn: "Comes in",
    loudnessShare: (ratio) =>
      `${Math.round(ratio * 100)}% as loud as the caller`,
    loudnessAutomatic: "Loudness automatic",
    atStart: "From the start",
    whenCalled: (action) => `When the agent calls ${action}`,
    // Every trigger kind in its own words; a kind a newer release adds shows by its own name.
    comesInLabel: (trigger) => {
      const { kind, tool, seconds, on_call: onCall, p } = trigger || {};
      switch (kind || "always") {
        case "always":
          return ACTORS_COPY.worldActors.atStart;
        case "on_tool":
          return onCall > 1
            ? `On call ${onCall} of ${tool}`
            : ACTORS_COPY.worldActors.whenCalled(tool);
        case "after_seconds":
          return `After ${seconds ?? 0} second${seconds === 1 ? "" : "s"}`;
        case "contacted":
          return "When contacted";
        case "probability":
          return `In about ${Math.round((p ?? 1) * 100)}% of calls`;
        default:
          return kind.replace(/_/g, " ");
      }
    },
    ifNoAnswer: "If they do not answer",
    graderMaterial: "Grader material",
    othersPrivate: (fields, count) =>
      `What the ${count === 1 ? "other actor keeps" : `${count} other actors each keep`} to themselves: ${fields}`,
    // What the view's inputs are called on screen; an input a newer release adds shows by its own name.
    inputs: {
      "actor.name": "Name",
      "actor.role": "Role",
      "actor.persona": "Persona",
      "actor.goal": "What they want",
      "actor.constraints": "What they may agree to and keep to themselves",
      "actor.behaviour": "How they behave",
      "actor.prompt": "Their prompt",
      "actor.surface": "How they reach the agent",
      "actor.knowledge": "What they know",
      "scenario.instruction": "What they do in this scenario",
      "scenario.knowledge": "What they know in this scenario",
      "caller.situation":
        "The caller's situation, without the caller's own details",
    },
    form: {
      createTitle: "Add someone in the world",
      moreDetails: "More details (not used by runs yet)",
      lessDetails: "Hide more details",
      moreDetailsHelp:
        "Saved with the actor, but a run plays them from each scenario's actions, so these do not change what happens yet.",
      editTitle: (name) => `Edit ${name}`,
      id: "Id",
      // The rule's words are the schema's own, when it sends them.
      idHelp: (rule) =>
        `Made from the name; change it now if you like. ${rule ? `${rule.charAt(0).toUpperCase()}${rule.slice(1)}. ` : ""}It never changes once saved.`,
      idTaken: (name) => `Already used by ${name}; choose another id`,
      idInvalid: (rule) =>
        rule ? `Use ${rule}` : "This id isn't allowed; choose another",
      permittedUnknown: (ids) =>
        `${ids.join(", ")} ${ids.length === 1 ? "is not an action" : "are not actions"} of this world, so a run cannot use ${ids.length === 1 ? "it" : "them"}; remove ${ids.length === 1 ? "it" : "them"} or pick from the list`,
      idFixed:
        "Fixed once saved, so scenarios and past runs keep pointing at this actor.",
      name: "Name",
      nameHelp: "What others call them, such as Priya Raman",
      role: "Role (optional)",
      roleRequired: "Role",
      roleHelp:
        "Who they are, in a few words, such as a manager or a family member",
      atLimit: (max) => `At most ${max} characters`,
      kind: "Kind (optional)",
      kinds: { person: "A person", agent: "Another agent", system: "A system" },
      parent: "Brought in by (optional)",
      parentHelp: "Someone already in the world who brings them in, if anyone",
      nobody: "No one",
      goal: "What they want (optional)",
      goalHelp:
        "Their own aim, which is not the agent's task, such as approve only refunds she can justify",
      mayAgree: "May agree to (optional, one per line)",
      private: "Keeps to themselves (optional, one per line)",
      escalateWhen: "Hands over when (optional, one per line)",
      stopWhen: "Is done when (optional, one per line)",
      facts: "What they know (optional, one per line)",
      factsHelp: "Facts they can use, such as the refund limit is $500",
      style: "How they behave (optional)",
      styleHelp: "Their manner, such as brisk and decides quickly",
      answers: "Answers",
      immediately: "Straight away",
      afterDelay: "After a delay",
      seconds: "Seconds",
      permitted: "World actions they may take (optional)",
      permittedHelp:
        "Pick from this environment's actions. The world refuses anything else they try and records the attempt.",
      noWorldActions:
        "This environment has no world actions: its tools run inside the agent, so no one in the world can act here.",
      trigger: "Comes in",
      triggerHelp: "When they first act in a call",
      timeout: "Waits, in seconds (optional)",
      timeoutHelp:
        "How long the world waits for them; 0 waits as long as the call lasts",
      fallback: "If they do not answer",
      fallbacks: { silent: "Nothing happens", decline: "They decline" },
      answeredBy: (name) => `${name} answers instead`,
      fallbackLabel: (id) =>
        ACTORS_COPY.worldActors.form.fallbacks[id] || `${id} answers instead`,
      channel: "How they join the conversation",
      noChannel: "Doesn't speak in the conversation",
      play: "How they act in the world",
      plays: {
        mocked: "Only the actions each scenario gives them",
        live: "Decide their own actions when reached",
        both: "Both",
      },
      playHelp:
        "Deciding their own actions, they read what they see below and choose within what they may do; the world still refuses anything they may not.",
      noChannelHelp:
        "They act only through the world's actions. Pick how they join to have them speak too.",
      loudness: "How loud, next to the caller (optional)",
      loudnessAutomatic: (low, high) =>
        `Automatic: each call picks between ${low} and ${high} of the caller's voice`,
      loudnessFixed: (share) => `${share} of the caller's voice`,
      voice: "How they sound (optional)",
      voiceHelp:
        "Picks their voice on a call; left empty, a voice is chosen for them",
      gender: "Gender",
      genders: { "": "Any", male: "Male", female: "Female" },
      ageGroup: "Age",
      ageGroups: {
        "": "Any",
        "18-31": "Under 32",
        "32-49": "32 to 49",
        "50-59": "50 to 59",
        "60+": "60 or older",
      },
      accent: "Accent",
      accents: ["", "American", "British", "Indian", "Australian", "Neutral"],
      anyAccent: "Any",
      changedElsewhere:
        "The actors were changed elsewhere while you were editing. This form now has the latest version with your edits kept. Check them and save again.",
      removedElsewhere:
        "This actor was removed elsewhere while you were editing, so these edits can't be saved.",
      cancel: "Cancel",
      discardTitle: "Discard changes?",
      discardBody: "What you typed here won't be saved.",
      discard: "Discard",
      save: "Save to draft",
    },
  },
};
