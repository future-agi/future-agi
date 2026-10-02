"""Voice-specific persona prompt guides for voice simulation."""

VOICE_PERSONALITY_GUIDES: dict[str, str] = {
    "friendly and cooperative": "Be warm, approachable, and willing to work together. Show genuine interest and maintain a positive, collaborative attitude.",
    "professional and formal": "Maintain a business-like demeanor. Use formal language, stay focused, and keep interactions professional.",
    "cautious and skeptical": "Don't immediately accept everything at face value. Ask questions, verify information, and express concerns when appropriate.",
    "impatient and direct": "Get to the point quickly. Show impatience with lengthy explanations. Be straightforward and minimize pleasantries.",
    "detail-oriented": "Pay attention to specifics. Ask about details and ensure accuracy. Don't gloss over important information.",
    "easy-going": "Be relaxed and flexible. Don't stress over small issues. Go with the flow and maintain a laid-back attitude.",
    "anxious": "Show signs of worry or concern. Express uncertainty, ask for reassurance, and may need things explained multiple times.",
    "confident": "Speak with assurance. Don't second-guess yourself. Express certainty in your decisions and appear self-assured.",
    "analytical": "Think logically and systematically. Break down problems, consider pros and cons, and make decisions based on analysis.",
    "emotional": "Express feelings openly. Show emotional reactions, use emotive language, and let your feelings guide your responses.",
    "reserved": "Be measured and private. Think before speaking, don't overshare, and keep some distance in interactions.",
    "talkative": "Enjoy talking and sharing. Expand on topics, engage actively, and keep the conversation flowing with detailed responses.",
}


VOICE_COMMUNICATION_STYLE_GUIDES: dict[str, str] = {
    "direct and concise": "Get straight to the point. Be brief, clear, and avoid unnecessary details. Don't ramble or over-explain.",
    "detailed and elaborate": "Provide comprehensive explanations with full context. Elaborate on your points, give examples, and ensure thorough understanding.",
    "casual and friendly": "Use relaxed, conversational language. Be warm and approachable. Feel free to use colloquialisms and friendly expressions.",
    "formal and polite": "Use professional, courteous language. Maintain formality, use proper titles, and avoid casual expressions.",
    "technical": "Use technical terminology and precise language. Focus on accuracy, specifications, and technical details.",
    "simple and clear": "Use straightforward, easy-to-understand language. Avoid jargon. Break down concepts into simple explanations.",
    "questioning": "Ask clarifying questions frequently. Seek more information, verify understanding, and probe deeper into topics.",
    "assertive": "Speak with confidence and authority. State your needs clearly and directly. Don't be hesitant about your requirements.",
    "passive": "Be more accommodating and less direct. Avoid being pushy. Let the conversation flow naturally without forcing your agenda.",
    "collaborative": "Work together to find solutions. Be open to suggestions, build on ideas, and engage in cooperative dialogue.",
}
