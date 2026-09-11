from concurrent.futures import ThreadPoolExecutor

from agentic_eval.core.utils.model_config import ModelConfigs

from ee.agenthub.prompt_template_agent.prompts import (
    template_cot_response_prompt,
    template_plan_prompt,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import (
    calculate_score,
    eval_instruction_process_data_format,
    get_criteria_judegement_score,
    get_summary_judgement,
    normalize_val,
)
from agentic_eval.core.utils.message_generator import prompt_message_generator

EVAL_TEMPLATE_THREAD_WORKER_COUNT = 2


class EvalPromptTemplateLLM:
    def __init__(
        self,
        model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
        max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
        provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
        llm=None,
        check_internet=False,
    ):
        if llm:
            self.llm = llm
        else:
            self.llm = LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )
        self.check_internet = check_internet
        if self.check_internet:
            self.online_llm = LLM(
                model_name=ModelConfigs.INTERNET_SEARCH.model_name,
                temperature=ModelConfigs.INTERNET_SEARCH.temperature,
                max_tokens=ModelConfigs.INTERNET_SEARCH.max_tokens,
                provider=ModelConfigs.INTERNET_SEARCH.provider,
            )

    def score_template_chat(self, criteria_breakdown, chat_history):
        return self.get_score_conversation_parallel(criteria_breakdown, chat_history)

    def solve_template_problem_with_planning(
        self, eval_instruction, template_data, max_iterations=1
    ):
        plan = self.generate_template_plan(eval_instruction, template_data)
        solution = self.generate_cot_response(eval_instruction, template_data, plan)

        return solution

    # model="openai/gpt-4o"

    def generate_cot_response(self, question, template_data, plan):
        prompt = template_cot_response_prompt.format(question=question, plan=plan)
        prompt = self.get_required_keys_for_prompt(
            template_data=template_data, prompt=prompt
        )
        messages = prompt_message_generator(
            #  template_plan_prompt.format(question=question, text_inputs=template_data)
            prompt
        )
        if self.check_internet:
            return self.online_llm._get_completion_content(messages=messages)
        else:
            return self.llm._get_completion_content(messages=messages)

    def generate_template_plan(self, question, template_data):
        prompt = template_plan_prompt.format(question=question)
        prompt = self.get_required_keys_for_prompt(
            template_data=template_data, prompt=prompt
        )
        messages = prompt_message_generator(
            #  template_plan_prompt.format(question=question, text_inputs=template_data)
            prompt
        )

        return self.llm._get_completion_content(messages=messages)

    def format_data_for_template(self, data):
        formatted_data = []
        # Loop through the data and extract question-answer pairs with context
        for i in range(0, len(data), 2):
            if (data[i]["role"] == "user" and data[i + 1]["role"] == "assistant") or (
                data[i]["role"] == "assistant" and data[i + 1]["role"] == "user"
            ):
                if data[i]["role"] == "user":
                    user_template = data[i]["content"]
                    variables = data[i].get("variables")
                    context = data[i].get("context")
                    prompt_template = data[i].get("prompt_template")
                    output = data[i + 1]["content"]
                else:
                    user_template = data[i + 1]["content"]
                    variables = data[i + 1].get("variables")
                    context = data[i + 1].get("context")
                    prompt_template = data[i + 1].get("prompt_template")
                    output = data[i]["content"]

                data_item = {
                    "user_template": user_template,
                    "output": output,
                    "context": context,
                    "variables": variables,
                    "prompt_template": prompt_template,
                }
                formatted_data.append(data_item)
        return formatted_data

    def get_required_keys_for_prompt(self, template_data, prompt):
        # Build the kwargs dictionary dynamically, excluding missing keys and formatting variables as a string
        template_parts = []

        if template_data.get("user_template"):
            template_parts.append(f"Input: {template_data['user_template']}")

        if template_data.get("output"):
            template_parts.append(f"Output: {template_data['output']}")

        if template_data.get("variables"):
            variables_str = "\n".join(
                f"{k}:{v}" for k, v in template_data["variables"].items() if v
            )
            template_parts.append(f"Variables:\n{variables_str}")

        if template_data.get("prompt_template"):
            template_parts.append(
                f"Prompt Template: {template_data['prompt_template']}"
            )

        if template_data.get("context"):
            template_parts.append(f"Textual Information: {template_data['context']}")

        template_parts.append(prompt)

        formatted_prompt = "\n\n".join(template_parts)

        return formatted_prompt

    def get_score_conversation_parallel(self, eval_instructions, chat_history):
        template_data = self.format_data_for_template(chat_history)
        # template_data = chat_history
        # Preparing list of (data_item, instruction_pair) for parallel processing
        eval_instructions = eval_instruction_process_data_format(eval_instructions)
        if type(eval_instructions[0]) == list:
            task_list = [
                (data_item, instruction_pair[0])
                for data_item in template_data
                for instruction_pair in eval_instructions
            ]
        else:
            task_list = [
                (data_item, instruction_pair)
                for data_item in template_data
                for instruction_pair in eval_instructions
            ]
        # print(len(task_list))

        def process_instruction(data_item, eval_instruction):
            solution = self.solve_template_problem_with_planning(
                eval_instruction, data_item
            )
            return calculate_score(self.llm, solution)

        with ThreadPoolExecutor(
            max_workers=EVAL_TEMPLATE_THREAD_WORKER_COUNT
        ) as executor:
            results = list(
                executor.map(lambda args: process_instruction(*args), task_list)
            )

        total_score, judgments = get_criteria_judegement_score(
            eval_instructions, results
        )
        num_inst = len(task_list)

        if type(eval_instructions[0]) == list:
            min_score = sum(
                (-2) * instruction_pair[1]
                for data_item in template_data
                for instruction_pair in eval_instructions
            )
            max_score = sum(
                (2) * instruction_pair[1]
                for data_item in template_data
                for instruction_pair in eval_instructions
            )
        else:
            min_score = (-2) * num_inst
            max_score = (2) * num_inst
        total_score = normalize_val((min_score, max_score), (0, 1), total_score)

        summary_judgement = get_summary_judgement(self.llm, judgments)

        return {
            "score": total_score,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }


if __name__ == "__main__":
    agent_task = EvalPromptTemplateLLM()
    c = criteria_breakdown = [
        "Assess the generated email's adherence to the provided email sample format, including structure, tone, and style.",
        "Evaluate the email's content relevance to the conversation context and query, ensuring all key points from the chat history are addressed.",
        "Analyze the email's language quality, including grammar, spelling, and appropriate level of formality based on the conversation and sample.",
        "Examine the email for completeness, checking if it includes all necessary components such as subject line, greeting, body, and closing.",
        "Judge the overall effectiveness of the email in achieving its intended purpose, considering factors such as clarity, persuasiveness, and professionalism.",
    ]
    data_item = [
        {
            "user_template": """
Hi #client_name,

Thank you for meeting with me today. It was a pleasure to learn about #organization_name and the ways that our platform could be beneficial to your business.

Please click here to view our pricing page, choose a plan, and sign up: https://www.contractorcommerce.com/pricing/

Friendly reminder that you can save 15% by choosing to pay upfront for the year.

Here are a few contractors that are currently utilizing our software with great Call to Action buttons:
https://grovehvac.com/: https://grovehvac.com/
<Include a list of contractors with their URLs.>

Here is the link to our demo site so you can experience our technology first hand: <Insert demo site link>

Please feel free to reach out with any additional questions. <Add a closing statement about future follow-up or availability.>

Best,

Person
Account Executive, Contractor Commerce
""",
            "ai_template": """
Hi Tim,

Thank you for meeting with me today. It was a pleasure to learn about Aim High HVAC and the ways that our platform could be beneficial to your business.

Please click here to view our pricing page, choose a plan, and sign up: https://www.contractorcommerce.com/pricing/

Friendly reminder that you can save 15% by choosing to pay upfront for the year.

Here are a few contractors that are currently utilizing our software with great Call to Action buttons:

- https://grovehvac.com/
- https://premieraircolorado.com/
- https://travis-crawford.com/

Here is the link to our demo site so you can experience our technology first hand: https://demo.contractorcommerce.com

Please feel free to reach out with any additional questions. I’ll follow up with you next week to see if you have any further questions or need assistance with anything.

Best,

Lisa Forrest


""",
            "context": """
                    Lisa Forrest: And they in two weeks generated 25 leads, sold the system for $15,000 and then that's continued ever since then he sat down with Paul and our success manager and said we had a tech in the home doing a maintenance. The homeowner went to our website, walked through the quote tool and the office got, and they're like, this name sounds familiar. So they looked it up and sure enough they're like oh my gosh, we have a tech there doing the maintenance. And that gentleman bought a new system by getting a quote online and they installed it three days later. And he was same thing. He was. I was skeptical, I was skeptical about this and I, you know, just trying these new things and this whole online thing kind of scares me. But you guys proved me wrong. And he goes, even if I just generate, like you mentioned, with Linux, if I generate one system that I can install per month with this, it's a bonus.
Tim Evans: I'd be happy with just one lead. I would at least know it's driving something that I would at least know, wow, something's working kind of, you know, and so one of the things that like, I'm very familiar, like if you Google Linux in my area, if you said Linux dealer in my area, and then if they ask you to put in the zip code, put in 80125 and then see what comes up for Linux. Okay, so Linux part store Linux, maybe the locate dealer right there, that locate h vac systems dealers and then, okay, save home. So put in 80125. And I just want to, so you can see summit came up number one and they flip flopped. That sometimes I come up, sometimes they come up. But if you look at the difference between me and me and Summit, they're 4.65 306, we're 575 star, I think right there. If somebody is searching, they pick me, right? But I noticed something on you guys in something that I saw on your e commerce thing. That in the button there where it says number one, the Nate, the blah blah blah, you guys have a box in there.
Tim Evans: Is that, is that like an instant quote thing? Yep. So they're going to, that's another what Linux is going to do for premier dealers. So let me pull up this screenshot. 1 second. I have it in my. See, you would think that me being the number one dealer in this area because I am the number one over summit. If you're, if you're picking, you gonna pick a 364.6 reviews or a 575 star review, most people are gonna pick us, but I'm not getting traffic from it. So I'm going, wow, I'm number one on their site in this area and I don't get anybody calling say I got you from Linux website. So now I'm going, okay. E commerce is going to be there. Now what's that going to get me? It's going to give me nothing. So if they're giving this to me or doing it, how are they going to drive people to that site?
Lisa Forrest: So the instant estimate and the buy filter button underneath your record when a customer clicks that they're going to come right to your website here.
Tim Evans: Okay.
Lisa Forrest: They're not going to Lennox's. That will go to your website and then to your filter store.
Tim Evans: But they're not, they're still not driving them to me though, that's what I'm saying is I'm not getting any traction from being number one on their site now. So that's not going to get me any traction either, you know, so I'm a more. That's all I'm saying is I don't know how excited I am with the integration with Linux or your partnership with Linux when I already am number one on their site there and I don't get anything. So I'm more like, let's forget that then because. So for me, I'm doing business with you guys, right? I can do any filter. I don't even have to be a Linux filter, correct.
Lisa Forrest: Nope. You get, you get full entirety from April air to carrier Brian Dynamic. If you're a dynamic filter installer.
Tim Evans: Okay.
Lisa Forrest: All these brands are available to you.
Tim Evans: Okay. I'm more interested now than I was when we started. So. And I'm not bagging on Linux. I like Linux. I really do. They're an amazing company. But they are not driving any business to me. I do for them way more than they do for me. And that's what it should be. Right? They're a manufacturer. They're trying to dabble in this stuff. And that's great. I love it. But I wish I had some results from it. That's all.
Tim Evans: I just wish I had one call a month. It would be awesome if I just had one person call and go, hey, I got you from the Linux website. I see you have 506 five star reviews. It's amazing. We'd love to get a quote from you. Oh, as a matter of fact I didn't need to do that. There was an instant quote thing there. So is that going to be on my, on my thing automatically or do I have to be part of you guys to have that?
Lisa Forrest: I do, yeah, you'd have to work with us for them to add it.
Tim Evans: And they don't a problem with us not being 100% loyal? I guess not, because nobody is 100% loyal to them. So I mean, I know that. So, okay. So my big thing is I don't want to do it halfway and go, well, okay, I'm not service tight, so I can't do that. So I'd have to have a, who would my software people have to have a conversation with, with your company to see if we can do what servicetitan is doing.
Lisa Forrest: So let's, if you could give me, connect me to that person, I'll get my product and head engineer on my team to kind of get an understanding of how it works and see if there's an open API that we can make them communicate.
Tim Evans: Okay. And that would be the next step. Is there levels of the SAS description on this or is it just strictly like, hey Tim, if your filters is this much and if your quotes this much, and do you guys get a cut of the filter cost too? Or is it just a subscription that you're getting?
Lisa Forrest: So let me, we've got three levels and what I would recommend is either the core advantage, the core package is the filters and selling things like maintenance, tune up, duct cleaning service. If you wanted to sell a drain cleaning, you could do that on the floor. The advantage includes the quote tool. That includes where someone can go online, walk through, get a quote. There is a 2% transaction fee that we take off the transaction of the filter store or the service that's sold. We don't take a percentage of the system sales. So if someone gets a quote, you get the lead, you go out and sell them a system for $15,000. That all stays with aim high. The only cost associated with that is the $10 per journey.
Tim Evans: So explain the $10 per journey feed. If that is a lead that I get, it's $10 for each one.
Lisa Forrest: Yep. Or you could pay no lead fee on the unlimited package.
Tim Evans: So even somebody just clicking on Facebook and going, I do that all the time on these ones. I get up. So it costs them $10 every time I click on it.
Lisa Forrest: So if you're putting your name and your phone number and you're going through their process, the entirety and you see pricing on one of our customers, yeah, that will change in terms bucks. But what they're probably doing, if you're getting a phone call or if you're not, they're probably disputing it. You can dispute the lead. So if someone goes through and puts in Rocky road and a bad phone number and you call that number and it's spam, you can dispute the lien in our system and we don't charge you the $10. Okay. And our system automatically disputes, duplicates. And if someone starts the journey, goes through, enters name, phone number, address, and they drop off halfway. We don't charge you $10, but you still actually have access to that data within our system and any information that they started along with their ip address.
Tim Evans: Okay, so I will, I will have that. So why so much on the 99, that 22, why is it so much more of a setup fee for that? It almost seems like it'd be less of a setup fee for that one to commit.
Lisa Forrest: Thousand bucks a month. The key difference between those two is one is the leafy that I mentioned. But here we have the pre built journey. So we already have that journey established name, phone number, address. This package we just created back in September, because we have a lot of companies that do multifaceted. They do plumbing, they do electrical. They want a way to capture leads for generators. Another one of my customers I brought on last year, GSM, they did the 499. We're averaging over 50 leads a month with our tools. So they were paying $500 a month on leads. So then they upgraded and they say, well, we see the benefit with this. We want to be able to generate leads for duct cleaning and dryer vent cleaning. So we created a custom journey for that specifically. So there's a lot of one on one with our product team to create a journey. So that's a little bit.
Tim Evans: So. And with that one, if I water treatment, tankless water heaters, all that, it wouldn't cost me extra subscription for that as long as it's on my platform. Right? Or my, my website.
Lisa Forrest: Yeah. No, you want it so you could do the water heaters here. If you wanted to create a custom water treatment journey, you could do that on the 999.
Tim Evans: Okay. And, and then you don't, you don't pay a per lead fee at all. It says bill monthly. There's an asterisk there. What is, what is the asterisk go to?
Lisa Forrest: Because you can say 19 or 19. Good Grace, Lisa, 15% if you sign up for the year and you're just billed once. So you save almost $1,000 here and then almost, let's call it 1800 bucks.
Tim Evans: I could pay, I could pay upfront the whole year and save 15%.
Lisa Forrest: Yep.
Tim Evans: Okay.
Lisa Forrest: Yep.
Tim Evans: Yeah.
Lisa Forrest: And then another. Are you part, so you're obviously part of the cat program. Do you get co op, co op dollars from Lennox?
Tim Evans: Yeah, I do.
Lisa Forrest: So you can co op this up to 60%. So you submit the invoice, Linux is going to pay you 60%, refund that back to your Linux bill.
Tim Evans: And when I do this, and this means nothing, but this automatically goes on to my dashboard for Linux prozen. Right. Or their Linux search engine.
Lisa Forrest: So it will, that's coming right now. Our legal team, Linux is legal team, they're hashing out the ends of it. So we're hoping by summertime that button will be on the Linux locator.
Tim Evans: Okay, so can I give your contact to my person and see if he would, you know.
Lisa Forrest: Yeah.
Tim Evans: Try to separate me with you guys because I just, you know, in full disclosure, I said I'm a board member of that company, I'm an investor in that company and I'm helping build that platform. Primarily I'll sell finishes for my own company, but they're signing up many, many many contractors across the country right now. And we have some really cool stuff going on. And I think this has some value, but also not sure that they're not doing something like this also, you know, so I don't know. I said I don't want to pitch you guys against each other, but I think, I mean, honestly, this will cost me more. Double what it cost me for my software platform. Double. And if I did it, I would do the unlimited because I dont play the $10. That would drive me nuts. If I had to dispute and do all that crap, it would drive me nuts. So I would do the upfront investment for the whole year because I wouldnt be interested in disputing $10, you know, stepping over dollars to pick up dimes. I mean it would drive me nuts. But now that I know it costs $10, I'm going to do this to all my competitors. No, I just, it pops up on my feed and I'm like, what are these guys doing now, you know, that's kind of my attitude.
Lisa Forrest: You know, I think there's one I, you know, premier air, yeah, Reed Borton, they're one that uses us. And he, his biggest thing is because the traffic in Denver, I mean, you, you obviously witness it every day so he doesn't have to send a customer that, you know, kind of just shopping them and send a salesman to that home, which is travel time, sitting in traffic at the kitchen table. Hey, go to our website, pre qualifies the client. And a lot of the time they're doing things through Zoom, you know, they're selling the systems to pre qualify the customer. Like, hey, here's our price and let us know if you want to set up an appointment. So everybody's got a different strategy, right? Like, you know, Lee's error uses it. Like, this is our price. If you're interested, let's move forward.
Tim Evans: If I did a monthly and say three months goes in, I go, this just ain't doing it. Do, am I locked in for a year?
Lisa Forrest: We ask for a year commitment and I'll tell you two reasons why and then I'll give you an asterisk. Well, a couple reasons why. One, we kind of want to weed out the contractors that are just going to do this. Just to think that this is going to make them grow their business. We want serious buyers in the sense that this is something that you have to invest in, your team's invested in. It's part of your marketing process. It's part of your team's process. The ones that just think this is going to do something to grow their business, we want to weed those guys out.
Tim Evans: Well, and that's why I said, when you say part of our marketing process, so there are other costs to this. I have to drive people to this. Like with Facebook. I can't. Just let me show, you know, I'll. Tell you people to me, I'm driving people to this.
Lisa Forrest: So when I say part of your marketing process, anytime you're talking about something, you're mentioning, hey, by the way, we have an online store and so I'll show you and what you're doing going on. Whoops. Travis Crawford. Why am I. There we go. This is where they have been able to generate, and this cost them nothing. If I go to their website now, offering instant quotes that cost them, you know, they're just doing a post. Here's the link. Customer clicks that, it comes right to their website, they're getting a quote that costs them nothing. And they've generated a bunch from that, you know, if you're doing, you know, if you've got your, your customers in your CRM.
Lisa Forrest: We have a customer, Lynn Strmer, that is in, oh goodness, where are they located? They did an email campaign to renew all their memberships. They generated $55,000 of new renewals of maintenance from our system by just doing an email blast. So you could do that with filters, you could do that with any service. You could do that with getting a quote. So, I mean, and that costs contractors nothing. That's using the tools that they have, the ones that do all the marketing with all that stuff. Yes. I mean they're there, but it's just including the link right off your website.
Tim Evans: Yeah. Okay, well I'm gonna, I'm sure you got other stuff coming up too. I've, I'm gonna talk to him. I'll have a meeting with him today or tomorrow and, and discuss this. I already told him I was meeting with you guys because we kind of talked about this and uh, I'm interested. Um, but I said I just, I trust him a lot on this, this stuff because he's an expert on this field. Uh, and I just want to make sure I'm not doing something that, I just started spending a lot of money on SEO and I'm now thinking I'm going to be, and I just want to make sure I'm not driving people to help you guys out. No offense to you guys, but you're supposed to help me out in this, not me, help you guys out in this, so. Or Linux. And I don't want to drive people to Linux. And the lead goes to one of my competitors. So I definitely want to make sure it's customizable for my company. And it sounds like it is.
Lisa Forrest: Yep. And one of the benefits too, you'll see is SEO optimization. Every part of our store, you can customize the content and the tags within the store which helps with SEO. And then, so that's the benefit. What I'm going to do after this, I have a meeting here at three. And you back to back to back. But by tonight I'm going to send you a nice email with just a breakdown. I'm going to give you some websites that have us plugged in. I'm going to give you the demo site so that you can show him how it works and what it looks like. And then I'll link my cell phone calendar, all that in an email. And then when he books something, I'll work to get with my team and, and we'd love to work with you and I appreciate your time and have me open minded.
Tim Evans: And I say, listening to my spiel per se. Yeah, I just like said I'm, I'm getting a little disgruntled with what's going on. The h vac world, I've been in it for 35 years and it used to be a pretty wholesome field and it's not anymore and it's, unfortunately it's not and it kind of sucks if you were like in the medical profession and you see that they're doing open heart surgery on people that don't need it because there's high value dollar, right. That didn't happen 50 years ago but it does now. Root canals get done because they make a lot of money on and you don't need a root canal. It's kind of disheartening. And those are people you trust. We used to be very trusted in this industry and we're not anymore.
Tim Evans: And it sucks to see. And I'm trying to be an honest, you know, person for my company. I'm trying to inspire the next generation of guys and we're being bombarded with just scumbag things and the people are in this industry are pretty bad and I'm kind of disgruntled on it. And then when I see the manufacturers doing the same thing and even leading us towards being bought out by venture capitalists it just drives me nuts. And that is within Linux's organization. They do that you know, their dealer me is crawling with those people crawling and they bring them in and they know what they're doing and I'm just, I'm pretty disheartened by it, you know. And I said I don't even think I'll go, ever go another dealer meeting to be honest with you.
Tim Evans: I just, you get bombarded with venture capitalists and it's just I'm getting disgruntled with, with what's going on. You know, I want to, I want to, I want to do it right. And so that's why I have to be so careful that I'm not, I'm literally joined an organization, service Nation alliance because we get great buying power worth and we get all this stuff and all they talk about is how are you going to sell your company? What's your succession plan? It's like first of all it's none of your damn business. I've got my own succession plan. I'm good. But that's all they want to talk about and they're introducing you to these people that they're trying to buy your business, and it just, you can't get away from it, you know?
Tim Evans: So I'm just getting to the point where I'm locked in my business down going, and we're not going to be exposed to this crap anymore, you know? And so that's just where I'm coming from on. This is, it just seems like every time I turn around, it's venture capitalists here and service titan here, and I'm just like, holy crap, like, I can't get away from it. And so I'm trying to do the right thing for my business to grow and scale. But how do I get away from these people, you know? I want to grow my business without selling it. Sorry, I'm not, not interested in selling. I got an amazing team that I want them to work with me. Not for some venture capitalists in, you know, Delaware or Connecticut or Florida, you know, or Oklahoma.
Tim Evans: And these are, these are where these people, they're trying to buy, you know, all the time. And so that's what, whatever I do, I have to be very honest with people and go, okay, like, who's your, who are you connected to it? What's your motivations and all that, you know? And it makes me seem jaded, but you have to be, you know, in this day and age. So I said, I'll never, I'll probably never go on another dealer meeting or another dealer trip. I'm just so, so disgruntled by them, you know? And, and I definitely would never send my people. Guy took seven people to that thing and they all came out of there just going, man, it was just, man, three years ago, it was great.
Tim Evans: This year, it just seemed like the same old stuff. And it was all, and they all got the same thing out of it. I did. So it's, it's kind of like, I don't know, I'm kind of bummed out about a little bit, right. You know, because it used to be dealer means about guys like me, and now it's not. It's about big companies. And so I'm, I'm, I'm trying to get away from it a little bit, you know? So this right here, I think I like the elements of it. As long as I can customize it and not be at the end of it and go, oh, now you have to have me come out because I literally sit here and watch these ads on there. Why have a scumbag dealer come out? Are you sick of these guys and this and that and then at the end of it, it goes, set up an appointment for us.
Tim Evans: Come out. It's like, I would be just like, that is just so phony to me, you know? So I would want to be very upfront in my process. This is a bulk. We still need to come out, but this, and that's not what these guys are doing with it, though. So I'm, I'm pretty disgruntled with that, too, you know? So it's like one of my competitors here has one that shows a slimy sales guy that you don't want this guy in your house and blah, blah, blah. And that's who they are. Exactly. That's that company. They're the ones you don't want in their house. It's just so funny that they're basically, it could be their own guy that they're saying, don't let me in your house. You know? So I just don't see that. So. All right, well, I'll look for your email and I'll talk to him and we'll go from there.
Lisa Forrest: Perfect. Tim, thank you for your time. I really appreciate it. And we'll be in touch soon.
Tim Evans: Thank you. Bye.
Lisa Forrest: Have a great evening. Bye.

""",
        }
    ]
    sc = agent_task.score_template_chat(criteria_breakdown=c, chat_history=data_item)
    print(sc)
