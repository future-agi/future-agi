BATCH_PROMPT = """This is Batch {batch_try} of this plan. Implement ADVANCED TAXONOMY-BASED DIVERSITY to ensure maximum variation and robustness across all dimensions.

**CRITICAL TAXONOMY-DRIVEN DIVERSITY REQUIREMENTS:**

**1. DEMOGRAPHIC VARIATION MANDATE:**
- Rotate through different age groups, cultural backgrounds, and socioeconomic levels
- Ensure geographic diversity (urban/rural, different regions/countries)
- Include varied educational and professional backgrounds
- Avoid demographic clustering or repetition

**2. BEHAVIORAL COMPLEXITY REQUIREMENTS:**
- Alternate communication styles: direct/indirect, formal/informal, assertive/passive
- Vary personality expressions: introverted/extroverted, analytical/intuitive
- Include different interaction patterns: collaborative/competitive, patient/urgent
- Ensure behavioral authenticity across different contexts

**3. CONTEXTUAL DIVERSITY MANDATE:**
- Rotate situational factors: emergency/routine, personal/professional, public/private
- Vary time dynamics: rushed/leisurely, deadline-driven/relaxed
- Include different social dynamics: power relationships, peer interactions, family contexts
- Ensure contextual appropriateness and authenticity

**4. LINGUISTIC SOPHISTICATION REQUIREMENTS:**
- Vary expertise levels: novice, intermediate, expert, cross-domain specialist
- Alternate formality spectrum: academic, business, conversational, intimate
- Include regional variations: dialects, cultural idioms, generational language
- Ensure linguistic authenticity and appropriateness

**5. EMOTIONAL AUTHENTICITY MANDATE:**
- Rotate emotional states: excited, frustrated, confused, satisfied, anxious, confident
- Vary stress levels: calm, mildly stressed, highly pressured, crisis mode
- Include satisfaction ranges: delighted, pleased, neutral, disappointed, angry
- Ensure emotional consistency with context and demographics

**6. EDGE CASE AND ROBUSTNESS INTEGRATION:**
- Include statistical outliers and unusual combinations
- Incorporate boundary conditions and error scenarios
- Add cross-cultural edge cases and format variations
- Ensure comprehensive coverage of rare but realistic scenarios

**7. INTERDEPENDENCY CONSISTENCY:**
- Maintain logical field relationships and correlations
- Ensure temporal sequence consistency where applicable
- Preserve contextual dependencies and role-based differences
- Maintain hierarchical structure integrity

**HUMAN-LIKE GENERATION GUIDELINES:**
- Generate data that reflects authentic human communication across all demographic and cultural groups
- Include natural imperfections appropriate to each demographic and context
- Vary writing styles based on age, culture, education, and situation
- Add realistic human elements: personal opinions, cultural references, generational expressions
- Include natural inconsistencies that humans exhibit in real communication
- Make interactions feel authentic with appropriate interruptions, clarifications, and flow patterns
- Ensure cultural sensitivity and authenticity in all variations

**BATCH DIVERSITY VALIDATION:**
Before generating each data point, ensure it differs from previous points in at least 3 of the following dimensions:
- Demographic profile (age, culture, background)
- Behavioral characteristics (communication style, personality)
- Contextual factors (situation, time pressure, social dynamics)
- Linguistic features (formality, expertise, regional variation)
- Emotional state (mood, stress level, satisfaction)
- Edge case elements (outliers, boundary conditions, error scenarios)

Here are the instructions for each datapoint to be generated in this batch. One instruction corresponds to one data point."""

GENERATION_PROMPT = """You are an advanced synthetic data generator specialized in creating realistic, human-like data. Your role is to generate high-quality synthetic data that mirrors authentic human communication and behavior patterns.

**CORE PRINCIPLES:**
1. **Human Authenticity**: Generate data that reflects how real people actually communicate, think, and behave
2. **Natural Imperfections**: Include realistic human elements like typos, informal language, emotional expressions
3. **Diverse Communication Styles**: Vary between formal, casual, technical, emotional, rushed, thoughtful styles
4. **Cultural Authenticity**: Include appropriate cultural references, slang, and regional variations
5. **Realistic Inconsistencies**: Mirror natural human inconsistencies in tone, detail level, and perspective

**ANTI-PATTERNS TO AVOID:**
- Generic placeholder text (e.g., "partner123", "example@email.com", "Lorem ipsum")
- Overly perfect or robotic language patterns
- Unrealistic consistency across all data points
- Artificial or template-like responses
- Null, empty, or meaningless values
- Repetitive or formulaic content structures

Do not introduce inconsistencies or errors that would not naturally occur in human communication. Ensure all generated data is meaningful, contextually appropriate, and adheres to the specified constraints.

**Generation Requirements:**
<requirements>
{requirements}
</requirements>

**Data Schema:**
<schema>
{schema}
</schema>

**Constraints:**
<constraints>
{constraints}
</constraints>

**Plan:**
<plan>
{plan}
</plan>

**Generation Parameters:**
Batch size: {batch_size}
Seed: {seed}

**CRITICAL CONSTRAINT COMPLIANCE:**
BEFORE generating any data, you MUST analyze each constraint and ensure STRICT adherence:

For each field in the constraints, follow these rules EXACTLY:
- **Type Compliance**: Generate values that match the specified data type exactly
- **Length Constraints**: If min_length/max_length specified, stay within bounds PRECISELY
- **Value Constraints**: If specific values are listed, ONLY use those exact values
- **Format Requirements**: Follow any specified formats (email, phone, date) exactly
- **Range Constraints**: For numerical fields, stay within specified min/max ranges
- **Pattern Constraints**: Follow any regex patterns or format specifications exactly
- **Custom Properties**: Follow ALL user-defined properties (min_words, max_sentences, contains, starts_with, ends_with, case, format, pattern, etc.)
- **Word/Sentence Limits**: Respect min_words, max_words, min_sentences, max_sentences if specified
- **Content Requirements**: Follow contains, starts_with, ends_with, exclude properties exactly
- **Case Requirements**: Follow case properties (upper, lower, title) precisely
- **Format Specifications**: Follow format properties (email, phone, url, date) exactly
- **Pattern Matching**: Ensure generated content matches any regex patterns specified

**DETAILED GENERATION INSTRUCTIONS:**

1. **MANDATORY Constraint Validation Per Field:**
   - **STEP 1**: Read the constraint for each field carefully
   - **STEP 2**: Identify the exact requirements (type, length, values, format)
   - **STEP 3**: Generate values that meet ALL specified constraints
   - **STEP 4**: Double-check each generated value against its constraints

2. **Field-Level Data Generation (CONSTRAINT-FIRST):**
   - **Text Fields**: 
     * Check min_length and max_length constraints FIRST
     * Generate content within exact character/word limits
     * If specific values listed, use ONLY those values
   - **Categorical Fields**: 
     * Use ONLY the values specified in the "values" constraint
     * IMPORTANT -> Never generate values outside the specified list. USE VALUES EXACTLY AS PROVIDED.
   - **Numerical Fields**: 
     * Respect min/max ranges exactly
     * Use appropriate data type (int, float, etc.)
   - **Email Fields**: 
     * Generate realistic emails with proper format
     * Follow any domain restrictions if specified
   - **Date Fields**: 
     * Use proper date format as specified
     * Stay within any date range constraints

3. **MANDATORY Custom Property Compliance:**
   - **Word Count Properties**: 
     * min_words: Generate at least the specified number of words
     * max_words: Generate no more than the specified number of words
   - **Sentence Count Properties**:
     * min_sentences: Include at least the specified number of sentences
     * max_sentences: Include no more than the specified number of sentences
   - **Content Requirements**:
     * contains: MUST include the specified text/phrase
     * starts_with: MUST begin with the specified text
     * ends_with: MUST end with the specified text
     * exclude: MUST NOT contain any of the specified values
   - **Case Requirements**:
     * case: "upper" = ALL UPPERCASE
     * case: "lower" = all lowercase  
     * case: "title" = Title Case Format
   - **Format Requirements**:
     * format: "email" = valid email format (user@domain.com)
     * format: "phone" = valid phone format with digits/symbols
     * format: "url" = valid URL format (http://... or https://...)
     * format: "date" = proper date format (YYYY-MM-DD or specified format)
   - **Pattern Requirements**:
     * pattern: Follow the exact regex pattern specified
   - **Numerical Constraints**:
     * min_value: Generate values >= specified minimum
     * max_value: Generate values <= specified maximum
   - **Uniqueness Requirements**:
     * unique: Ensure values are unique across all records (if specified)

2. **Human-Like Communication Patterns:**
   - **Conversational Data**: Include natural speech patterns, interruptions, clarifications
   - **Email/Messages**: Vary formality levels, include signatures, greetings, typos
   - **Reviews/Feedback**: Include emotional language, personal experiences, varying detail levels
   - **Questions**: Range from simple to complex, include follow-ups and clarifications
   - **Responses**: Vary in completeness, some brief, some detailed, some tangential

3. **Realistic Imperfections (Include Strategically):**
   - **Typos**: 5-10% of text should contain realistic typos or autocorrect errors
   - **Grammar**: Occasional grammatical inconsistencies that humans make
   - **Abbreviations**: Use common abbreviations, acronyms, and internet slang appropriately
   - **Incomplete Thoughts**: Some responses may trail off or be interrupted
   - **Emotional Expressions**: Include excitement, frustration, confusion, enthusiasm

4. **Content Authenticity:**
   - **Specific Details**: Include realistic specific details rather than generic ones
   - **Personal Touch**: Add personal anecdotes, preferences, and experiences
   - **Cultural Context**: Include appropriate cultural references and context
   - **Time-Sensitive Content**: Reference current events, seasons, or time-appropriate content
   - **Domain Expertise**: Vary expertise levels from novice to expert perspectives

5. **Quality Assurance:**
   - **No Null Values**: Every field must contain meaningful, non-empty content
   - **No Placeholder Text**: Avoid any form of placeholder or template text
   - **Contextual Consistency**: Ensure all fields in a record are contextually related
   - **Realistic Relationships**: Maintain logical relationships between related fields
   - **Diversity**: Ensure significant variation across all generated records

6. **Validation Checks:**
   - Verify no field contains null, empty, or placeholder values
   - Ensure all text is meaningful and contextually appropriate
   - Check that numerical values are realistic for their context
   - Validate that categorical values are from expected ranges
   - Confirm that related fields maintain logical consistency

**HUMAN-LIKE EXAMPLES BY DATA TYPE:**

**Conversations:**
- Include natural pauses, "um", "uh", "you know"
- Add interruptions: "Sorry, what I meant was..."
- Include emotional reactions: "That's amazing!" or "Ugh, that's frustrating"

**Emails:**
- Vary subject lines from formal to casual
- Include realistic signatures and contact info
- Add typos and autocorrect errors naturally
- Use different greeting styles: "Hi", "Hello", "Hey there"

**Reviews/Feedback:**
- Include personal experiences and specific details
- Vary rating explanations from brief to detailed
- Add emotional language and personal opinions
- Include comparisons to other experiences

**Technical Content:**
- Mix expert and novice perspectives
- Include some technical jargon and some simplified explanations
- Add realistic troubleshooting steps and solutions
- Include both successful and failed attempts

**Output Requirements:**
- Generate exactly {batch_size} complete, realistic data records
- Each record must be unique and contextually coherent
- All fields must contain meaningful, non-placeholder content
- Include appropriate human-like imperfections and variations
- Ensure cultural and contextual authenticity

**Output Format:**
{{
  "1": {{
    "field_1": <realistic_value_with_human_touch>,
    "field_2": <contextually_appropriate_value>,
    ...
  }},
  "2": {{
    "field_1": <varied_style_realistic_value>,
    "field_2": <naturally_imperfect_but_meaningful_value>,
    ...
  }},
  ...
}}

**FINAL VALIDATION CHECKLIST:**
✓ **NO EXTRA EXPLANATION**: Generate only the required content, no additional text or explanations
✓ **CONSTRAINT COMPLIANCE**: Every field strictly follows its specified constraints
✓ **TYPE ACCURACY**: All values match their required data types exactly
✓ **LENGTH COMPLIANCE**: Text fields respect min_length/max_length bounds
✓ **VALUE RESTRICTIONS**: Categorical fields use only specified values
✓ **FORMAT ADHERENCE**: Emails, dates, phones follow required formats
✓ **RANGE COMPLIANCE**: Numerical values stay within specified ranges
✓ **CUSTOM PROPERTY COMPLIANCE**: All user-defined properties are followed exactly
✓ **WORD/SENTENCE LIMITS**: min_words, max_words, min_sentences, max_sentences respected
✓ **CONTENT REQUIREMENTS**: contains, starts_with, ends_with, exclude properties followed
✓ **CASE REQUIREMENTS**: upper, lower, title case properties applied correctly
✓ **PATTERN MATCHING**: All regex patterns and format specifications matched
✓ **NUMERICAL CONSTRAINTS**: min_value, max_value properties respected
✓ **UNIQUENESS**: Unique constraints maintained across records where specified
✓ No null, empty, or placeholder values
✓ All content is realistic and human-like
✓ Appropriate variation in style and tone
✓ Natural imperfections included strategically
✓ Cultural and contextual authenticity maintained
✓ Logical consistency between related fields
✓ Meaningful diversity across all records

RULES:

1. Return only valid JSON matching the required format. 
2. Do not include any extra text in the beginning or end. 
3. Do not start with ```json and end with ``` just return the json structure


"""

INS_PROMPT = """You are an advanced instruction generator specializing in creating robust, diverse synthetic data through sophisticated taxonomy-based guidance. Your role is to craft detailed, multi-dimensional instructions that leverage the full spectrum of human variation and real-world complexity.

**ADVANCED INSTRUCTION FRAMEWORK:**

**1. TAXONOMY-DRIVEN INSTRUCTION DESIGN:**
Each instruction must incorporate multiple dimensions of variation:
- **Demographic Dimension**: Age, gender, cultural background, socioeconomic status, geographic location
- **Behavioral Dimension**: Communication style, personality traits, interaction patterns, decision-making approaches
- **Contextual Dimension**: Situational factors, environmental conditions, time constraints, social dynamics
- **Linguistic Dimension**: Formality level, technical expertise, regional dialects, generational language patterns
- **Emotional Dimension**: Mood states, stress levels, satisfaction ranges, emotional intelligence levels

**2. ROBUST VARIATION STRATEGIES:**
Implement systematic variation across:

**A. Demographic Diversity:**
- **Age Ranges**: Gen Z (digital natives), Millennials (tech-savvy), Gen X (transitional), Boomers (traditional)
- **Cultural Backgrounds**: Western, Eastern, Latin, African, Middle Eastern, Indigenous perspectives
- **Geographic Variations**: Urban/rural, developed/developing regions, climate-specific contexts
- **Socioeconomic Levels**: Various income brackets, education levels, professional backgrounds

**B. Behavioral Complexity:**
- **Communication Styles**: Direct/indirect, high-context/low-context, formal/informal, assertive/passive
- **Personality Types**: Introverted/extroverted, analytical/intuitive, detail-oriented/big-picture
- **Interaction Patterns**: Collaborative/competitive, patient/urgent, methodical/spontaneous

**C. Contextual Authenticity:**
- **Situational Factors**: Emergency/routine, personal/professional, public/private settings
- **Time Dynamics**: Rush situations, leisurely interactions, deadline pressures, seasonal contexts
- **Social Dynamics**: Power relationships, peer interactions, customer service scenarios, family contexts

**D. Linguistic Sophistication:**
- **Expertise Levels**: Domain novice, intermediate user, subject matter expert, cross-domain specialist
- **Formality Spectrum**: Academic formal, business professional, casual conversational, intimate personal
- **Regional Variations**: Dialect differences, cultural idioms, local expressions, generational slang

**E. Emotional Authenticity:**
- **Emotional States**: Excited, frustrated, confused, satisfied, anxious, confident, overwhelmed
- **Stress Levels**: Calm and collected, mildly stressed, highly pressured, crisis mode
- **Satisfaction Ranges**: Delighted, pleased, neutral, disappointed, angry

**3. EDGE CASE AND BOUNDARY INSTRUCTION COVERAGE:**
Include instructions for:
- **Statistical Outliers**: Unusual combinations, rare scenarios, extreme cases
- **Error Scenarios**: System failures, user mistakes, incomplete information, conflicting data
- **Boundary Conditions**: Minimum/maximum values, empty states, overflow situations
- **Cross-cultural Edge Cases**: Cultural misunderstandings, translation issues, format differences
- **Temporal Edge Cases**: Historical references, future projections, time zone complications

**4. INTERDEPENDENCY INSTRUCTION MODELING:**
- **Field Relationships**: Instructions that maintain logical consistency across related fields
- **Temporal Sequences**: Time-dependent instruction chains, cause-and-effect relationships
- **Contextual Dependencies**: Situation-specific instruction variations, role-based differences
- **Hierarchical Structures**: Parent-child instruction relationships, nested complexity levels

**INSTRUCTION GENERATION REQUIREMENTS:**
- Each instruction should describe a realistic scenario for generating one authentic data point
- Emphasize human-like characteristics: emotions, personal experiences, cultural context
- Include guidance for natural imperfections: typos, informal language, varying expertise levels
- Specify diverse communication styles: formal, casual, technical, emotional, rushed, thoughtful
- Ensure instructions cover edge cases and authentic human variations
- Prevent repetition by specifying unique scenarios, perspectives, and contexts
- Include cultural diversity and authentic regional/demographic variations
- Ensure instructions explicitly mention how to satisfy user-defined properties (word counts, format requirements, content specifications, etc.)
- **TAXONOMY INTEGRATION**: Each instruction must explicitly incorporate elements from the plan's taxonomy focus

***CRITICAL***: Include specific guidance for ALL custom properties defined in constraints

**HUMAN-AUTHENTICITY GUIDELINES:**
- Specify realistic personas with authentic backgrounds and motivations
- Include emotional context and personal stakes in scenarios
- Guide toward natural speech patterns and communication styles
- Encourage authentic cultural references and contextual details
- Specify varying levels of expertise and knowledge
- Include realistic time pressures and situational constraints

**ANTI-PATTERN PREVENTION:**
- Explicitly instruct against placeholder text (e.g., "user123", "example.com")
- Prevent overly formal or robotic language patterns
- Avoid unrealistic perfection in communication
- Discourage template-like or formulaic responses
- Prevent null, empty, or meaningless content generation

**IMPORTANT: Your task is to create detailed scenario-based instructions that will be used as user-prompts in the next step where we generate authentic, human-like datasets in conjunction with domain-specific knowledge**

**Generation Requirements:**
<requirements>
{requirements}
</requirements>

**Data Schema:**
<schema>
{schema}
</schema>

**Constraints:**
<constraints>
{constraints}
</constraints>

**Plan:**
<plan>
{plan}
</plan>


**Output Requirements:**
- Return exactly {datapoints_per_plan} detailed, scenario-based instructions
- Each instruction should describe a unique, realistic human scenario
- Include specific persona details, emotional context, and situational factors
- Ensure cultural diversity and authentic communication styles across instructions
- Prevent any generic or template-like instruction patterns

**INSTRUCTION QUALITY STANDARDS:**
- Specify realistic personas with authentic backgrounds (age, profession, culture, expertise level)
- Include emotional context and personal motivations
- Describe specific situational constraints (time pressure, audience, purpose)
- Guide toward natural communication patterns and authentic imperfections
- Ensure each scenario is unique and contextually rich

**GENERATE {datapoints_per_plan} INSTRUCTIONS ONLY**
**Output Format:**
{{
   "1":{{"instruction for data point 1": "Generate a data point representing [specific demographic profile: age group, cultural background, socioeconomic level] in [detailed contextual scenario: setting, time pressure, social dynamics] who exhibits [behavioral characteristics: communication style, personality traits, interaction patterns]. This person has [expertise level and linguistic characteristics] and is experiencing [emotional state and stress level]. Include [specific taxonomy elements from plan focus], ensure [custom property compliance], and incorporate [authentic imperfections: typos, informal language, cultural expressions]. The scenario should reflect [edge cases or boundary conditions if applicable] while maintaining [interdependency requirements with other fields]."}},
   "2":{{"instruction for data point 2": "Create a data point featuring [different demographic combination] in [unique situational context] demonstrating [distinct behavioral patterns]. This individual [professional/educational background] communicates with [formality level and regional variations] while [emotional and stress context]. Incorporate [plan's secondary taxonomy dimensions], address [specific constraint requirements], and include [natural human variations: generational language, cultural idioms, expertise gaps]. Ensure [edge case coverage] and [field relationship consistency]."}},
   "3":{{"instruction for data point 3": "Generate data representing [another demographic variation] experiencing [complex contextual scenario] with [specific behavioral and linguistic characteristics]. Include [cultural authenticity elements], [temporal dynamics], and [emotional authenticity]. Address [custom properties and constraints], incorporate [robustness measures], and ensure [taxonomic diversity from plan focus]. Add [realistic imperfections and variations] while maintaining [logical field interdependencies]."}},
      ...
   "{datapoints_per_plan}":{{"instruction for data point {datapoints_per_plan}": "[Comprehensive instruction incorporating full taxonomy framework: demographic diversity, behavioral complexity, contextual authenticity, linguistic sophistication, emotional range, edge case coverage, custom property compliance, and interdependency modeling]"}},
}}

**VALIDATION CHECKLIST FOR EACH INSTRUCTION:**
✓ **TAXONOMY INTEGRATION**: Incorporates elements from the plan's primary and secondary taxonomy dimensions
✓ **DEMOGRAPHIC DIVERSITY**: Specifies unique, realistic demographic profiles with authentic backgrounds
✓ **BEHAVIORAL COMPLEXITY**: Includes varied communication styles, personality traits, and interaction patterns
✓ **CONTEXTUAL AUTHENTICITY**: Describes specific situational factors, time dynamics, and social contexts
✓ **LINGUISTIC SOPHISTICATION**: Guides toward appropriate formality levels, expertise, and regional variations
✓ **EMOTIONAL AUTHENTICITY**: Includes emotional states, stress levels, and satisfaction ranges
✓ **EDGE CASE COVERAGE**: Addresses boundary conditions, outliers, and error scenarios where applicable
✓ **INTERDEPENDENCY MODELING**: Ensures logical consistency and relationships between fields
✓ **ROBUSTNESS MEASURES**: Includes stress testing scenarios and boundary condition coverage
✓ **CULTURAL SENSITIVITY**: Encourages appropriate cultural authenticity and regional awareness
✓ **NATURAL IMPERFECTIONS**: Includes guidance for realistic human variations and imperfections

✓ IMPORTANT -> **CUSTOM PROPERTY COMPLIANCE**: Explicitly addresses all user-defined constraints and properties. Ensure the values chosen in the instruction comply with the constraints provided. USE VALUES EXACTLY AS SPECIFIED.


STRICTLY RETURN JSON COMPATIBLE FORMAT.
"""

# instruction = "{instruction_text}"

query_distillation_prompt = """You are a helpful assistant that rewrites long instructions into concise semantic search queries for a retrieval-augmented generation system.  
Only output the distilled query, without quotes or any additional explanation.  
Keep it focused on the core intent and key entities.

Instruction:  
{instruction}

Return your output in the following JSON format:
{{
    "Query": "distilled query within quotes"
}}

Present your findings in a JSON RFC 8259 compatible format.

RETURN ONLY THE JSON OBJECT

OUTPUT SHOULD START WITH {{ AND END WITH }}
"""


DATA_QUALITY_VALIDATION_PROMPT = """You are an advanced data quality validator specializing in constraint compliance and human-like synthetic data. Your role is to rigorously validate generated data for constraint adherence, authenticity, completeness, and human-like characteristics.

**PRIMARY VALIDATION OBJECTIVES:**
1. **CRITICAL: Constraint Compliance**: Verify STRICT adherence to all field constraints
2. **Eliminate Null/Empty/Placeholder Content**: Identify and flag any null, empty, or placeholder values
3. **Ensure Human Authenticity**: Validate that content reflects realistic human communication
4. **Verify Contextual Coherence**: Ensure all fields in each record are logically related
5. **Check Cultural Authenticity**: Validate appropriate cultural context and references
6. **Assess Natural Imperfections**: Ensure realistic human imperfections are present

**CRITICAL FAILURE PATTERNS TO DETECT:**
- Null, empty, or undefined values in any field
- Placeholder text (e.g., "example@email.com", "user123", "Lorem ipsum", "N/A", "TBD")
- Overly generic or template-like content
- Unrealistic perfection in language or formatting
- Inconsistent or illogical relationships between fields
- Artificial or robotic communication patterns
- Missing cultural context where appropriate
- Repetitive or formulaic content structures

**VALIDATION CRITERIA:**

Generated Data:
{generated_data}

Schema:
{schema}

Requirements:
{requirements}

Constraints:
{constraints}

**DETAILED VALIDATION CHECKS:**

1. **CRITICAL: Constraint Compliance Validation:**
   - **Type Validation**: Every field value must match its specified data type exactly
   - **Length Validation**: Text fields must respect min_length/max_length constraints precisely
   - IMPORTANT -> **Value Validation**: Categorical fields must use ONLY values from the specified list. USE VALUES EXACTLY AS PROVIDED.
   - **Format Validation**: Email, phone, date fields must follow required formats exactly
   - **Range Validation**: Numerical fields must stay within specified min/max ranges
   - **Pattern Validation**: Fields with regex patterns must match exactly
   - **Required Field Validation**: All mandatory fields must be present and valid

2. **Completeness Validation:**
   - Every field must contain meaningful, non-empty content
   - No null, undefined, or placeholder values allowed
   - All required fields must be populated with realistic data

2. **Content Authenticity Validation:**
   - Text should reflect natural human communication patterns
   - Names should be realistic and culturally appropriate
   - Email addresses should use believable domains and formats
   - Dates should be realistic and contextually appropriate
   - Numbers should make sense in their real-world context

3. **Human-Like Quality Validation:**
   - Communication should include natural variations in style and tone
   - Appropriate level of imperfections (typos, informal language)
   - Emotional expressions and personal touches where relevant
   - Cultural references and context where appropriate

4. **Contextual Coherence Validation:**
   - All fields in a record should be logically related
   - Maintain consistency in persona and context across fields
   - Ensure realistic relationships between related data points

5. **Diversity Validation:**
   - Sufficient variation across all generated records
   - No over-representation of specific patterns or values
   - Appropriate cultural and demographic diversity

**OUTPUT REQUIREMENTS:**
Provide a detailed validation report in the following JSON format:

{{
  "validation_status": "PASS|FAIL|NEEDS_REVISION",
  "overall_quality_score": <float_0_to_1>,
  "validation_results": {{
    "constraint_compliance": {{
      "status": "PASS|FAIL",
      "compliance_score": <float_0_to_1>,
      "type_violations": <integer>,
      "length_violations": <integer>,
      "value_violations": <integer>,
      "format_violations": <integer>,
      "range_violations": <integer>,
      "failed_records": [<list_of_record_ids>]
    }},
    "completeness": {{
      "status": "PASS|FAIL",
      "null_empty_count": <integer>,
      "placeholder_count": <integer>,
      "failed_records": [<list_of_record_ids>]
    }},
    "authenticity": {{
      "status": "PASS|FAIL", 
      "human_like_score": <float_0_to_1>,
      "artificial_patterns_detected": <integer>,
      "failed_records": [<list_of_record_ids>]
    }},
    "contextual_coherence": {{
      "status": "PASS|FAIL",
      "coherence_score": <float_0_to_1>,
      "inconsistent_records": [<list_of_record_ids>]
    }},
    "diversity": {{
      "status": "PASS|FAIL",
      "diversity_score": <float_0_to_1>,
      "repetitive_patterns": <integer>
    }}
  }},
  "specific_issues": [
    {{
      "record_id": "<id>",
      "field": "<field_name>",
      "issue_type": "constraint_violation|type_mismatch|length_violation|value_restriction|format_violation|range_violation|null_value|placeholder|artificial|incoherent|repetitive",
      "description": "<detailed_description>",
      "suggested_fix": "<specific_recommendation>"
    }}
  ],
  "recommendations": [
    "<specific_actionable_recommendations_for_improvement>"
  ]
}}

Rules: 
1. Return only valid JSON matching the required format. 
2. Do not include any extra text in the beginning or end. 
3. Do not start with ```json and end with ``` just return the json structure"""

DATA_REPAIR_PROMPT = """You are an expert data repair specialist focused on fixing synthetic data to make it more human-like and authentic. Your role is to identify and repair specific issues in generated data while maintaining the original intent and schema compliance.

**REPAIR OBJECTIVES:**
1. **Fix Null/Empty Values**: Replace with meaningful, contextually appropriate content
2. **Remove Placeholder Text**: Replace generic placeholders with realistic, specific content  
3. **Enhance Human Authenticity**: Add natural imperfections and human-like characteristics
4. **Improve Contextual Coherence**: Ensure all fields relate logically within each record
5. **Increase Diversity**: Reduce repetitive patterns while maintaining quality

**REPAIR GUIDELINES:**

Original Data with Issues:
{original_data}

Validation Issues Identified:
{validation_issues}

Schema:
{schema}

Requirements:
{requirements}

**SPECIFIC REPAIR INSTRUCTIONS:**

1. **Null/Empty Value Repairs:**
   - Replace null/empty values with realistic, contextually appropriate content
   - Ensure new content fits the field type and constraints
   - Make content consistent with other fields in the same record

2. **Placeholder Text Repairs:**
   - Replace "example@email.com" with realistic email addresses
   - Replace "user123" with authentic usernames or names
   - Replace "Lorem ipsum" with meaningful, contextual text
   - Replace "N/A", "TBD", "TODO" with appropriate content

3. **Human Authenticity Enhancements:**
   - Add natural typos and informal language where appropriate
   - Include emotional expressions and personal touches
   - Vary communication styles (formal, casual, technical, emotional)
   - Add cultural context and authentic references

4. **Contextual Coherence Repairs:**
   - Ensure all fields in a record tell a consistent story
   - Align persona characteristics across all fields
   - Maintain logical relationships between related data

5. **Diversity Improvements:**
   - Vary language patterns and communication styles
   - Introduce different cultural perspectives and backgrounds
   - Ensure unique scenarios and contexts for each record

**OUTPUT REQUIREMENTS:**
Return the repaired data in the exact same JSON structure as the original, with all identified issues fixed:

{{
  "1": {{
    "field_1": "<repaired_realistic_value>",
    "field_2": "<enhanced_human_like_value>",
    ...
  }},
  "2": {{
    "field_1": "<contextually_coherent_value>",
    "field_2": "<culturally_authentic_value>",
    ...
  }},
  ...
}}

**REPAIR VALIDATION CHECKLIST:**
✓ No null, empty, or placeholder values remain
✓ All content is realistic and human-like
✓ Contextual coherence maintained across fields
✓ Appropriate cultural authenticity included
✓ Natural imperfections strategically added
✓ Diversity enhanced while maintaining quality
✓ Schema compliance preserved

Rules: 
1. Return only valid JSON matching the required format. 
2. Do not include any extra text in the beginning or end. 
3. Do not start with ```json and end with ``` just return the json structure"""

VALIDATION_PROMPT = """You are a deterministic data quality validator. Your role is to make the final acceptance decision for generated data based on the planning framework and detailed analysis results.

**Generation Requirements:**
<requirements>
{requirements}
</requirements>

**Data Schema:**
<schema>
{schema}
</schema>

**Constraints:**
<constraints>
{constraints}
</constraints>

**Generation Parameters:**
Batch size: {batch_size}

**Analysis Results:**
{analysis}

**Quality Thresholds:**
<thresholds>
{quality_thresholds}
</thresholds>

**Validation Instructions:**
1. **Schema and Constraints Compliance**:
   - Validate that every record conforms to the schema.
   - Ensure all constraints, including type, range, uniqueness, and interdependencies, are satisfied.

2. **Diversity and Coverage**:
   - Check for sufficient representation across the entire range of values for each field.
   - Confirm the presence of edge cases and under-represented patterns.

3. **Statistical Validation**:
   - Ensure that the distribution of data aligns with the specified parameters.
   - Validate stratified sampling and proportional representation of categorical and numerical values.

4. **Inter-field Relationships**:
   - Confirm that statistical and logical relationships between fields are preserved.
   - Verify that correlations, dependencies, and constraints are satisfied.

5. **Batch-Level Characteristics**:
   - Validate that the overall batch exhibits the required diversity and statistical characteristics.
   - Ensure no over-representation or skewed distributions unless explicitly defined in the constraints.

6. **Reproducibility and Quality**:
   - Confirm that the data generation process is reproducible.
   - Ensure quality thresholds are met, including precision, recall, and error tolerance for critical validations.

**Output Requirements:**
- Provide a detailed validation report summarizing:
  - Schema compliance
  - Constraint adherence
  - Diversity and coverage metrics
  - Statistical alignment
  - Inter-field relationship validation
  - Batch-level characteristics
- If the data fails validation, identify specific records or areas that require regeneration."""


PLANNING_PROMPT = """You are an advanced synthetic data generation strategist specializing in creating robust, diverse datasets through sophisticated taxonomy-based planning. Your role is to develop {num_plans} distinct generation strategies that maximize data variation, coverage, and real-world authenticity.

**Generation Requirements:**
<requirements>
{requirements}
</requirements>

**Data Schema:**
<schema>
{schema}
</schema>

**Constraints:**
<constraints>
{constraints}
</constraints>

**Generation Parameters:**
Batch size: {batch_size}

**ADVANCED TAXONOMY FRAMEWORK:**

**1. MULTI-DIMENSIONAL ANALYSIS:**
Analyze each field across multiple dimensions:
- **Data Type Dimension**: categorical, numerical, temporal, textual, boolean, composite
- **Semantic Dimension**: demographic, behavioral, contextual, technical, emotional
- **Complexity Dimension**: simple, compound, hierarchical, relational
- **Variation Dimension**: static, dynamic, conditional, probabilistic
- **Quality Dimension**: formal, informal, structured, unstructured, mixed

**2. ROBUST VARIATION STRATEGIES:**
For each strategy, implement multiple variation axes:

**A. Content Variation:**
- **Linguistic Diversity**: formal/informal, technical/layman, regional dialects, generational differences
- **Cultural Diversity**: geographic regions, ethnic backgrounds, socioeconomic levels, professional contexts
- **Temporal Diversity**: historical periods, seasonal patterns, time-of-day variations, lifecycle stages
- **Emotional Diversity**: sentiment ranges, stress levels, excitement, frustration, satisfaction
- **Expertise Diversity**: novice, intermediate, expert, domain-specific knowledge levels

**B. Structural Variation:**
- **Length Patterns**: ultra-short, short, medium, long, variable-length sequences
- **Format Patterns**: structured, semi-structured, unstructured, mixed formats
- **Completeness Patterns**: complete, partial, fragmented, over-detailed information
- **Consistency Patterns**: highly consistent, moderately consistent, inconsistent, contradictory

**C. Behavioral Variation:**
- **Communication Styles**: direct, indirect, assertive, passive, collaborative, competitive
- **Error Patterns**: typos, grammatical errors, autocorrect mistakes, intentional abbreviations
- **Interaction Patterns**: single-turn, multi-turn, interrupted, resumed conversations
- **Context Patterns**: personal, professional, emergency, routine, celebratory

**3. EDGE CASE AND BOUNDARY COVERAGE:**
Each strategy must include:
- **Statistical Outliers**: extreme values, rare combinations, unusual patterns
- **Boundary Conditions**: minimum/maximum values, empty states, overflow conditions
- **Error Scenarios**: malformed data, missing information, conflicting data
- **Real-world Anomalies**: unexpected inputs, system failures, user mistakes
- **Cross-cultural Edge Cases**: different naming conventions, date formats, cultural practices

**4. INTERDEPENDENCY MODELING:**
- **Field Correlations**: positive, negative, conditional, complex relationships
- **Temporal Dependencies**: sequence-dependent data, time-based correlations
- **Contextual Dependencies**: situation-dependent variations, role-based differences
- **Hierarchical Dependencies**: parent-child relationships, nested structures

**5. ROBUSTNESS TESTING DIMENSIONS:**
- **Stress Testing**: high-volume scenarios, rapid changes, system limits
- **Adversarial Testing**: intentionally difficult cases, edge combinations
- **Compatibility Testing**: cross-platform variations, version differences
- **Scalability Testing**: small-scale to large-scale variations

**STRATEGY GENERATION INSTRUCTIONS:**

Create {num_plans} distinct strategies, each focusing on different aspects of the taxonomy:

**Strategy Types to Include:**
1. **Demographic Diversity Strategy**: Focus on age, gender, location, cultural background variations
2. **Behavioral Complexity Strategy**: Emphasize different user behaviors, interaction patterns
3. **Linguistic Variation Strategy**: Cover different communication styles, formality levels, languages
4. **Temporal Dynamics Strategy**: Include time-based patterns, seasonal variations, lifecycle stages
5. **Edge Case Coverage Strategy**: Focus on boundary conditions, outliers, error scenarios
6. **Cross-Cultural Strategy**: Emphasize cultural differences, regional variations, local customs
7. **Professional Context Strategy**: Cover different industries, roles, expertise levels
8. **Emotional Range Strategy**: Include various emotional states, stress levels, satisfaction ranges
9. **Technical Complexity Strategy**: Range from simple to highly technical content
10. **Real-world Authenticity Strategy**: Focus on realistic imperfections, natural variations

**OUTPUT FORMAT:**
{{
    "1": {{
        "name": "Comprehensive strategy name reflecting focus area",
        "taxonomy_focus": {{
            "primary_dimension": "Main variation focus (demographic/behavioral/linguistic/temporal/edge-case/cultural/professional/emotional/technical/authenticity)",
            "secondary_dimensions": ["Supporting variation aspects"],
            "coverage_scope": "Specific scope of variation coverage"
        }},
        "column_strategies": {{
            "column_name": {{
                "type": "categorical|numerical|datetime|text|boolean|composite",
                "variation_approach": "Specific approach for this column",
                "value_ranges": ["Multiple ranges or categories to cover"],
                "edge_cases": ["Specific edge cases to include"],
                "cultural_variations": ["Cultural/regional variations if applicable"],
                "quality_levels": ["Different quality/formality levels"],
                "interdependencies": ["Relationships with other columns"]
            }}
        }},
        "generation_rules": [
            "Detailed rules for generating diverse, robust data",
            "Specific instructions for handling edge cases",
            "Guidelines for maintaining authenticity and variation",
            "Rules for ensuring proper interdependencies"
        ],
        "validation_criteria": [
            "Criteria for validating diversity and coverage",
            "Checks for edge case inclusion",
            "Validation of cultural and linguistic appropriateness",
            "Assessment of real-world authenticity"
        ],
        "robustness_measures": [
            "Specific measures to ensure data robustness",
            "Stress testing scenarios to include",
            "Boundary condition coverage requirements"
        ]
    }},
    ... // Repeat for all {num_plans} strategies
}}

**CRITICAL REQUIREMENTS:**
- Each strategy MUST cover different aspects of the taxonomy framework
- Ensure maximum diversity across all strategies combined
- Include comprehensive edge case coverage
- Maintain real-world authenticity and cultural sensitivity
- Provide specific, actionable generation rules
- Cover the full spectrum of possible variations within constraints
- Include robust validation and quality measures

**DIVERSITY MANDATE:**
No two strategies should focus on the same primary dimension. Ensure complementary coverage across all strategies to achieve maximum dataset robustness and variation.
Rules: 
1. Ensure strict adherence to the schema and constraints provided. For example, if language is specified as "Spanish", do not generate data in other languages. 
2. IMPORTANT -> If "values" are specified for a field, ensure all generated values are from that list. USE VALUES EXACTLY AS PROVIDED.
3. Return only valid JSON matching the required format. 
4. Do not include any extra text in the beginning or end. 
5. Do not start with ```json and end with ``` just return the json structure"""


## Prompts for seeded agent

EXAMPLE_ANALYSIS_PROMPT = """Analyze the following examples to extract patterns relevant to this generation plan.
Consider these dataset characteristics from the task taxonomy:

{dataset_characteristics}

Examples:
{examples}

Generation Plan:
{plan_details}

Extract patterns, relationships, and constraints that should be maintained in generated data.
Focus on the listed dataset characteristics.
Return only a JSON object with the analysis results."""

TASK_CLASSIFICATION_PROMPT = """Assume you are an expert at inferring datapoints provided to you. You will be given a list of datapoints and a list of task categories.

Analyze the datapoints and return the list of task categories that an llm can be improved on using the datapoints.

Task Categories:
{categories}

Datapoints:
{examples}

Return all the categories possible

Return the categories in a JSON RFC 8259 compatible format.
{{
  "categories": ["Reading comprehension", "Analytical reasoning", "Text modification", ...., "Text classification"],
  "confidence": {{
    "Reading comprehension": 0.80,
    "Analytical reasoning": 0.60,
    "Text modification": 0.20,
    ...
    "Text classification": 0.10
  }},
  "reasoning": "The provided examples primarily involve understanding and interpreting text passages, aligning strongly with Reading Comprehension. There's also an element of logical deduction required, suggesting Analytical Reasoning, albeit with lower confidence."
}}

"""

INSTRUCTION_GENERATION_PROMPT = """Given some datapoints, a category, and a dataset characteristic, generate specific instructions for data generation that will maintain this characteristic.

Datapoints:
{datapoints}

Category:
{category}

Characteristic:
{characteristic}

Generate clear, actionable instructions that:
1. Explain how to implement this characteristic in generated data
2. Provide specific guidelines and examples
3. Include any relevant constraints or patterns to follow

Output should only be the instructions.

Return the instructions in a clear, concise format."""


PREPARE_PAYLOAD_PROMPT = """
You are tasked with creating dataset properties based on given <<Prompt Name>>, <<Prompt Instruction>> (if available), and <<Variable Names>>. This analysis will help in understanding the structure and characteristics of the data, which is crucial for data validation, schema design, and data quality assurance.

Here are the provided details:

<Prompt Name>
{prompt_name}
</Prompt Name>

<Prompt Instruction> (if available)
{prompt_instruction}
</Prompt Instruction>

<Variable Names>
{variable_names}
</Variable Names>

The variable names provided above should correspond to the field in contraints section shown below. Each variable name is a column in the dataset.
Your job is to analyze the requirements in the dataset, its columns and infer its properties and constraints. You should provide:

{{
"requirements": {{
        "Dataset Name": "name of the dataset",
        "Dataset Description": "one line description of the dataset",
        "Objective": "what are we aiming to achieve from the dataset",
        "patterns": "if the dataset should follow any language patterns"
    }},
    "constraints": [
        {{
            "field": "variable 1",
            "type": "data type",
            "content": "one line description of the content",
            "values": possible categories/dynamic,
            ... additional properties
        }},
        ...,
        {{
            "field": "variable n",
            "type": "data type",
            "content": "one line description of the content",
            "values": possible categories/dynamic,
            ... additional properties
        }},
    ]
}}


EXAMPLE 1:

Input:
   <Prompt Name>
   {{Customer Complaint Response Template}}
   </Prompt Name>
   
   <Prompt Instruction>

   You are a customer service representative tasked with responding to a customer complaint email. Your goal is to address the customer's concerns professionally and offer an appropriate resolution based on company policies. Follow these steps to draft your response:

   This customer complaint is of type {{PROBLEM_DIFFICULTY}}.

   1. First, carefully read the customer's complaint email:
   <customer_email>
   {{CUSTOMER_EMAIL}}
   </customer_email>

   2. Next, review the company's policies and guidelines for handling customer complaints:
   <company_policies>
   {{COMPANY_POLICIES}}
   </company_policies>

   3. Analyze the complaint:
      - Identify the main issue(s) raised by the customer
      - Determine the severity of the problem
      - Check if the complaint violates any company policies or if it's eligible for compensation/resolution

   4. Draft your response following these guidelines:
      - Begin with a polite greeting and thank the customer for their feedback
      - Acknowledge the customer's concerns and express empathy
      - Apologize for any inconvenience caused, if appropriate
      - Explain the situation clearly and concisely, without making excuses
      - Offer a resolution that aligns with company policies

   </Prompt Instruction>

   <Variable Names>
   [PROBLEM_DIFFICULTY, CUSTOMER_EMAIL, COMPANY_POLICIES]
   </Variable Names>

Output:
   {{
      "requirements": {{
            "Dataset Name": "customer_complaint_email_dataset",
            "Dataset Description": "Dataset of complaints made by customers for various products by the company",
            "Objective": "Training material for new customer service representative",
            "patterns": "Should mimic how frustrated customers write emails and vary in difficulty"
         }},
         "constraints": [
            {{
                  "field": "PROBLEM_DIFFICULTY",
                  "type": "categorical",
                  "content": "Difficulty level of problem",
                  "values": "["Easy","Moderate","Difficult"]",
                  "min_length":"50 words",
                  "max_length":"150 words",
            }},
            
            {{
                  "field": "CUSTOMER_EMAIL",
                  "type": "string",
                  "content": "Email written by the customer describing their problem",
                  "values": "dynamic",
                  "min_length":"50 words",
                  "max_length":"150 words",
            }},
            {{
                  "field": "COMPANY_POLICIES",
                  "type": "string",
                  "content": "document describing company policy",
                  "values": "dynamic",
                  "min_length":"100 words",
                  "max_length":"150 words",
            }},
         ]
      }}

Remember, the goal is to provide a comprehensive understanding of the dataset's structure and characteristics, which can be used for data validation, schema design, and ensuring data quality.

If you cannot infer anything based on the provided details or beacuse the names are gibberish, please say "ENTER MEANINGFUL PROMPT AND VARIABLE NAMES"

Present your findings in a JSON RFC 8259 compatible format.

RETURN ONLY THE JSON OBJECT
OUPTUT SHOULD START WITH {{ AND END WITH }}
"""

COLUMN_GENERATION_PROMPT = """
You are an advanced synthetic data generator specialized in creating new, realistic columns for existing data. Your role is to expand a dataset by adding high-quality synthetic data that is contextually coherent with the existing row data.

**CORE PRINCIPLES:**
1. **Contextual Coherence**: The new column values MUST be logically consistent with the existing data in the row.
2. **Human Authenticity**: Generate data that reflects how real people actually communicate, think, and behave.
3. **Constraint Adherence**: Strictly follow all constraints provided for the new columns.

**ANTI-PATTERNS TO AVOID:**
- Generic placeholder text (e.g., "partner123", "example@email.com", "Lorem ipsum")
- Null, empty, or meaningless values.
- Values that contradict the existing `row_data`.
- Repetitive or formulaic content structures.

**CONTEXT:**

**Dataset Description:**
{requirements}

**Column Schema:**
{schema}

**Existing Row Data:**
{row_data}

**CONSTRAINTS FOR NEW COLUMNS:**
This section defines the requirements for the new columns to be generated.
{constraints}

{branch_context}

**DETAILED GENERATION INSTRUCTIONS:**

1.  **Analyze the Context**:
    *   Carefully review the **Dataset Description** to understand the overall purpose and theme of the dataset.
    *   Review the **Column Schema** to understand the data types and structure of the columns to be generated.
    *   Thoroughly examine the **Existing Row Data**. Identify the persona, context, and any impli
    cit relationships between the existing values. The new data must feel like it belongs to this specific row.

2.  **Adhere to Constraints**:
    *   For each new column defined in **Constraints**, you MUST follow the requirements precisely.
    *   Pay close attention to `type`, `content` description, `values` (if categorical), and any other specified properties (e.g., `min_length`, `max_length`).

3.  **Generate High-Quality Content**:
    *   **Be Specific and Realistic**: Avoid generic content. The generated values should be specific and sound authentic.
    *   **Maintain Persona**: The new data should match the voice, tone, and characteristics of the persona established in the `row_data`.
    *   **Ensure Logical Consistency**: The new values must make sense in the context of the existing data and the column schema. For example, if the existing data points to a specific geographic location, the new data should not contradict it.

**Output Requirements:**
- Generate values for the new columns as specified in the constraints.
- The output must be a single JSON object.
- The keys of the JSON object must be the `field` names from the `constraints`.
- The values should be the generated content for those fields.

**EXAMPLE OUTPUT FORMAT:**
{{
    "new_column_name_1": "generated_value_1",
    "new_column_name_2": "generated_value_2"
}}

**FINAL VALIDATION CHECKLIST:**
✓ **NO EXTRA EXPLANATION**: Generate only the required JSON content.
✓ **Contextual Consistency**: New values are consistent with `row_data` and `schema`.
✓ **Constraint Compliance**: Every new field strictly follows its specified constraints.
✓ **No Placeholders**: No null, empty, or placeholder values are used.
✓ **Authenticity**: The generated content is realistic and human-like.

**RULES:**
1. Return only a valid JSON object in RFC 8259 compatible format.
2. Do not include any extra text before or after the JSON object.
3. The output should start with `{{` and end with `}}`.
"""

REQUIREMENTS_INFERENCE_PROMPT = """
You are an expert at generating requirements for datasets.
You are given a missing requirement for a dataset.
You are also given a dataset and its sample.
Generate the missing requirement.

Input:
Missing Key: {key}
Dataset: {dataset_str}

Examples:
For Fintech Bot:
"Dataset Description": Fintech bot dataset simulating interactions between human and automated agent.
"Objective": Train an in house model based on query and response
"Patterns": Problems should cover different fintech concepts like trading, investments, savings, retirement with varying difficulty levels, and include detailed step-by-step solutions. Should be in conversational style

For Retention Emails:
"Dataset Description": "Dataset of complaints made by customers for various products by the company",
"Objective": "Training material for new customer service representative",
"patterns": "Should mimic how frustrated customers write emails and vary in difficulty"   

Output Should be in JSON RFC 8259 compatible format.
{{
   {key}: "Value to be generated"
}}

RETURN ONLY THE JSON OBJECT
OUPTUT SHOULD START WITH {{ AND END WITH }}
"""
