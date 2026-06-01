-- Speaking questions seed: 12 questions (4 per part)
-- Part 1: Interview (personal questions)
-- Part 2: Long Turn (topic cards)
-- Part 3: Discussion (abstract questions)

INSERT INTO question_bank (exam_type, task_type, difficulty, module, title, prompt_text) VALUES
-- PART 1: Interview (4 questions)
('speaking', NULL, 1, 'part1', 'Your Hometown',
 'Tell me about your hometown. What do you like most about it?'),
('speaking', NULL, 1, 'part1', 'Work or Studies',
 'Are you currently working or studying? What do you enjoy about it?'),
('speaking', NULL, 2, 'part1', 'Daily Routine',
 'Describe your typical daily routine. Has it changed much over the years?'),
('speaking', NULL, 2, 'part1', 'Hobbies and Interests',
 'What do you enjoy doing in your free time? How did you become interested in that?'),

-- PART 2: Long Turn (4 topic cards)
('speaking', NULL, 2, 'part2', 'A Memorable Journey',
 'Describe a memorable journey you have taken. You should say: where you went, who you went with, what you did during the journey, and explain why this journey was so memorable.'),
('speaking', NULL, 3, 'part2', 'An Important Decision',
 'Describe an important decision you made in your life. You should say: what the decision was, when you made it, why you had to make that decision, and explain how it affected your life.'),
('speaking', NULL, 2, 'part2', 'A Person You Admire',
 'Describe a person you admire. You should say: who this person is, how you know them, what they have achieved, and explain why you admire this person.'),
('speaking', NULL, 3, 'part2', 'A Book That Influenced You',
 'Describe a book that had a significant influence on you. You should say: what the book was, when you read it, what it was about, and explain why it influenced you so much.'),

-- PART 3: Discussion (4 follow-up questions — can pair with Part 2 topics)
('speaking', NULL, 3, 'part3', 'Travel and Tourism',
 'How has tourism changed in your country over the past decade? Do you think travel is essential for personal growth?'),
('speaking', NULL, 3, 'part3', 'Technology and Communication',
 'In what ways has technology changed how people communicate? Are people becoming more isolated because of technology?'),
('speaking', NULL, 2, 'part3', 'Education Systems',
 'What are the main differences between education systems in different countries? How important is university education in today''s job market?'),
('speaking', NULL, 3, 'part3', 'Environmental Issues',
 'What do you think are the most pressing environmental challenges today? Whose responsibility is it to address these issues — governments, businesses, or individuals?');
