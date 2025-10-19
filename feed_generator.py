from pydantic import BaseModel
from typing import List
from flask import Flask
from openai import OpenAI


# Base
class ScrollSectionComment(BaseModel):
    id: int
    author: str
    pfp_emoji: str
    likes: int
    content: str


class BaseScrollSection(BaseModel):
    id: int
    title: str
    author: str
    topic: int
    section: int
    likes: int
    background: str
    comments: List[ScrollSectionComment]


# Question
class ScrollQuestion(BaseScrollSection):
    question: str
    choices: List[str]
    correct_idx: int


# Reel
class ScrollReel(BaseScrollSection):
    video_prompt: str


# Post
class ScrollPostSlide(BaseModel):
    icon: str
    content: str


class ScrollPost(BaseScrollSection):
    topic: int
    section: int
    author: str
    title: str
    slides: List[ScrollPostSlide]


# Container
class ScrollTopic(BaseModel):
    title: str
    sections: List[str]


class ScrollTopicResponse(BaseModel):
    title: str
    topics: List[ScrollTopic]
    question: List[ScrollQuestion]
    reels: List[ScrollReel]
    posts: List[ScrollPost]


system_prompt = """
    Your job is to create a realistic instagram-type feed with educational topic the user requests. 

    Each topic consistents of several sections. Topic titles should also be simple For example: \"Data types\". You should only include material, not study methods. Sections should also be a simple name, like "Strings"
    
    After creating the outline for the course, generate the content of the feed. The feed consists of Questions, Posts, and Reels. They should also have an associated topic and section (0 indexed into the previously generated sections). Every type requires a made up author, title, likes, and comments. Author should be a username with alphanumeric characters and underscores, like an instagram username. Likes can be any number. Generate a list comments for every post. Comments should cover some common misunderstandings of the comment, and should be written in a gen-z style.
    
    Reels will be generated using the prompt you give. You should be very sparing with adding reels, they should only be for major topics. Write a prompt for a 12 second video. Generate a maximum of 5 reels, or less if possible. Your video prompt should include all the information necessary to create a full reel on the topic. The video should be gen-z style and the prompt should include some speaking outlines. Include the video title and author name. Make the videos funny, out of pocket, unexpected, with a very unique and unpredictable events. Try to include at least one person on a skateboard, but it should not be the main focus. You can also have the video be the conversation between several characters. Also include quick visuals of the content.
    
    Posts should cover most of the content. A post consists of multiple slides, each with an icon (emoji) and some content. Keep each slide brief and focussed.
    
    Questions are to quiz the user on the topic. Include an icon (emoji), the question text, 4 answer choices, and the 0-indexed correct answer. The question field should just include the text of the question, ending in a question mark. The question field should not be used as a title.
    
    For all id fields, increment a numeric id. The background is a unique value for the css "background" property for the post. Its a good idea to use a gradient with unique colors like "linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
"""


def generate_topics(client: OpenAI, prompt) -> ScrollTopicResponse:
    response = client.responses.parse(
        model="gpt-5-mini",
        input=[
            {"role": "system",
                "content": system_prompt},
            {
                "role": "user",
                "content": prompt,
            },
        ],
        text_format=ScrollTopicResponse)

    for i, topic in enumerate(response.output_parsed.topics):
        print(f"Topic {i + 1} - {topic.title}")
        for j, section in enumerate(topic.sections):
            print(f"\t{i+1}.{j+1} - {section}")

    return response.output_parsed.model_dump()


def generate_reel(client: OpenAI, prompt: str):
    video = client.videos.create_and_poll(prompt=prompt, seconds='12')
    if video.error is not None:
        print(video.error)
        return
    client.videos.download_content(video.id).write_to_file(f"{video.id}.mp4")
