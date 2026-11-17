// Static input data for model regression tests; no simulation runtime.
export const WRAPPED_CONVERSATIONS = [
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "Our whole team is locked out since this morning's SSO change. 50 people can't work. I'm the admin.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "I'm sorry about that, Sofia. Could you try clearing your browser cache and signing in again?",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "This isn't a cache issue. Nobody can sign in. We need an engineer now.",
      },
      {
        n: 4,
        role: "agent",
        content:
          "I understand. Here is our guide to resetting SSO settings: help.acme.ai/sso-reset.",
        evals: {
          helpfulness: "Fail",
        },
        tool_calls: [
          {
            name: "search_kb",
            args: {
              query: "sso reset",
            },
            result: [
              {
                id: "kb-12.4",
              },
            ],
            status: "ok",
          },
        ],
      },
      {
        n: 5,
        role: "customer",
        content: "Escalate this immediately, please.",
      },
      {
        n: 6,
        role: "agent",
        content:
          "I've noted your request. Is there anything else I can help with?",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "An enterprise-wide lockout (P1) was answered with self-help instead of an escalation.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "This is my third message about being charged $89 twice. Last two times I was told it would be fixed.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Sorry about the duplicate charge, Daniel. Refunds can take 5–7 business days to appear.",
        evals: {
          helpfulness: "Fail",
        },
        tool_calls: [
          {
            name: "lookup_customer",
            args: {
              email: "d.okafor@example.com",
            },
            result: {
              orders: ["A-6120"],
              tickets: ["T-46021", "T-46380"],
            },
            status: "ok",
          },
        ],
      },
      {
        n: 3,
        role: "customer",
        content: "It's been three weeks. I have two ticket numbers already.",
      },
      {
        n: 4,
        role: "agent",
        content:
          "I can see your tickets. Please allow a few more days for processing.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Third contact on the same problem with two open tickets; policy says escalate to billing as P2.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "The charger you sold me sparked and left a burn mark on my desk. My kid was right there.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "That sounds frightening, Grace. Please unplug the charger and stop using it.",
        evals: {
          helpfulness: "Pass",
        },
      },
      {
        n: 3,
        role: "customer",
        content: "I already did. What are you going to do about it?",
      },
      {
        n: 4,
        role: "agent",
        content:
          "You can return it within 30 days for a refund using our returns portal.",
        evals: {
          helpfulness: "Fail",
        },
        tool_calls: [
          {
            name: "search_kb",
            args: {
              query: "charger return",
            },
            result: [
              {
                id: "kb-3.1",
              },
            ],
            status: "ok",
          },
        ],
      },
      {
        n: 5,
        role: "customer",
        content:
          "A refund? Someone could have been hurt. I want to talk to someone responsible.",
      },
      {
        n: 6,
        role: "agent",
        content:
          "I understand your frustration. Is there anything else I can help with?",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "A product safety incident must go to the safety team as P1; the agent offered a routine return.",
    failing_turn: 4,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "Please delete all my data under GDPR.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Liam. I can help with your account settings. You can change your email preferences here.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to privacy (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "Someone placed an order on my account that I didn't make!",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Aisha. Could you confirm the order number so I can check it?",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to fraud (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "If this isn't sorted today I'm contacting my lawyer.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Omar. I'm sorry you feel that way. Let me see what I can do about the delivery.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to legal-safety (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "I want to speak to a manager about how my complaint was handled.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Samuel. I'm happy to help with your complaint myself. What happened?",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to support-lead (P3); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "Your checkout doesn't work with my screen reader. This is the second time I've reported it.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Ruth. Have you tried a different browser?",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to tier-2 (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "A $2,400 laptop order shows delivered but never arrived.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Kenji. Please check with your neighbours and around your property.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to carrier-claims (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "I'm filing a chargeback with my bank unless you refund me now.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Nadia. Refunds normally take 5–7 business days.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to billing (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "I got a phishing email with my order details. Were you hacked?",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Emma. We take security seriously. Please don't click unknown links.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to security (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "The shelf you sold me collapsed and my partner cut her hand.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Lucas. I'm sorry to hear that. You can request a replacement shelf.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to legal-safety (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "Your courier was abusive to me at the door.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Isabel. I'm sorry about that. Your parcel was delivered successfully.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to carrier-relations (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "Third time asking: cancel my subscription. The button doesn't work.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Ethan. You can cancel anytime from Account > Subscription.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to tier-2 (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "Our business account is locked and we can't place orders for the store.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Chloé. Please reset your password using the link on the sign-in page.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to enterprise-support (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "I've been charged four times for one order.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Noah. I can see your order. Is there anything else I can help with?",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to billing (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content:
          "My order for Saturday's wedding is missing. I need it by Friday.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Yuki. Delivery times are estimates and can vary.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to dispatch (P2); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
  {
    turns: [
      {
        n: 1,
        role: "customer",
        content: "Someone posted my address in a product review on your site.",
      },
      {
        n: 2,
        role: "agent",
        content:
          "Thanks for reaching out, Zoe. Thanks for letting us know. Reviews are moderated within 7 days.",
        evals: {
          helpfulness: "Fail",
        },
      },
      {
        n: 3,
        role: "customer",
        content:
          "That doesn't help. I need someone who can actually deal with this.",
      },
      {
        n: 4,
        role: "agent",
        content: "I understand. I've added a note to your account.",
        evals: {
          helpfulness: "Fail",
        },
      },
    ],
    resolution: "Fail",
    resolution_reason:
      "Needed an escalation to trust-safety (P1); the agent kept answering itself.",
    failing_turn: 2,
    channel: "chat",
  },
];
