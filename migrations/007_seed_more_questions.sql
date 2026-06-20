-- Additional IELTS questions seed: 57 questions (speaking + writing)
-- Speaking: 12 Part 1, 10 Part 2, 10 Part 3 = 32
-- Writing: 5 General Task 1, 5 Academic Task 1, 15 Task 2 = 25

INSERT INTO question_bank (exam_type, task_type, difficulty, module, title, prompt_text) VALUES

-- ========================================
-- SPEAKING PART 1 (12 questions, difficulty 1-3)
-- ========================================
('speaking', NULL, 1, 'part1', 'Your Home',
 'Let''s talk about your home. Where do you live and what do you like about it?'),
('speaking', NULL, 1, 'part1', 'Cooking',
 'Do you enjoy cooking? Why or why not?'),
('speaking', NULL, 1, 'part1', 'Music',
 'What kind of music do you listen to in your free time?'),
('speaking', NULL, 1, 'part1', 'Public Transport',
 'How often do you use public transportation?'),
('speaking', NULL, 1, 'part1', 'Books vs Movies',
 'Do you prefer reading books or watching movies? Why?'),
('speaking', NULL, 1, 'part1', 'Favorite Season',
 'What is your favorite season of the year? Describe it.'),
('speaking', NULL, 2, 'part1', 'Outdoor Activities',
 'Do you like to spend time outdoors? What do you do outside?'),
('speaking', NULL, 2, 'part1', 'Morning Routine',
 'How do you usually start your day?'),
('speaking', NULL, 1, 'part1', 'Gifts',
 'What kinds of gifts do you like to give to others?'),
('speaking', NULL, 2, 'part1', 'Museums and Galleries',
 'Do you enjoy going to museums or art galleries?'),
('speaking', NULL, 1, 'part1', 'Weather Preferences',
 'What is your favorite type of weather? Why?'),
('speaking', NULL, 2, 'part1', 'Study Habits',
 'Do you prefer to study alone or with others?'),

-- ========================================
-- SPEAKING PART 2 (10 cue cards, difficulty 2-4)
-- ========================================
('speaking', NULL, 2, 'part2', 'A Skill You Want to Learn',
 'Describe a skill you would like to learn. You should say: what it is, why you want to learn it, how you plan to learn it, and explain how it would benefit your life.'),
('speaking', NULL, 2, 'part2', 'A Time You Helped Someone',
 'Describe a time when you helped someone. You should say: who you helped, how you helped them, why they needed help, and explain how you felt afterward.'),
('speaking', NULL, 2, 'part2', 'A Place You Often Visit',
 'Describe a place in your city that you often visit. You should say: where it is, what you do there, why you go there often, and explain what makes it special.'),
('speaking', NULL, 3, 'part2', 'A Memorable Celebration',
 'Describe a memorable celebration or party you attended. You should say: when it was, who was there, what happened, and explain why it was memorable.'),
('speaking', NULL, 2, 'part2', 'A Useful Technology',
 'Describe a piece of technology you find useful. You should say: what it is, how you use it, why you find it useful, and explain how it has changed your life.'),
('speaking', NULL, 3, 'part2', 'A Personal Goal',
 'Describe a goal you have set for yourself. You should say: what the goal is, why you set it, what steps you are taking, and explain how achieving it will affect your life.'),
('speaking', NULL, 3, 'part2', 'A Tradition in Your Country',
 'Describe a tradition from your country. You should say: what the tradition is, when it happens, what people do, and explain why it is important.'),
('speaking', NULL, 2, 'part2', 'A Positive Friend',
 'Describe a friend who has had a positive influence on you. You should say: who they are, how you met, how they influenced you, and explain why their influence was positive.'),
('speaking', NULL, 3, 'part2', 'A Thought-Provoking Show',
 'Describe a movie or TV show that made you think. You should say: what it was, what it was about, why it made you think, and explain what you learned from it.'),
('speaking', NULL, 4, 'part2', 'A Challenge You Overcame',
 'Describe a challenge you faced and overcame. You should say: what the challenge was, how you dealt with it, what the outcome was, and explain how it changed you.'),

-- ========================================
-- SPEAKING PART 3 (10 discussion questions, difficulty 3-5)
-- ========================================
('speaking', NULL, 3, 'part3', 'Technology and Relationships',
 'How has technology changed the way people communicate? Do you think this is positive or negative overall?'),
('speaking', NULL, 4, 'part3', 'Government and Environment',
 'What role should governments play in protecting the environment?'),
('speaking', NULL, 3, 'part3', 'Remote Work Trends',
 'Why do some people prefer to work from home rather than in an office? What are the long-term effects of this shift?'),
('speaking', NULL, 3, 'part3', 'Encouraging Reading',
 'How can societies encourage young people to read more books?'),
('speaking', NULL, 4, 'part3', 'Education and Employment',
 'Do you think the education system prepares students adequately for the workforce? What could be improved?'),
('speaking', NULL, 3, 'part3', 'Tourism Impact',
 'What are the benefits and drawbacks of tourism for local communities?'),
('speaking', NULL, 4, 'part3', 'Social Media and Mental Health',
 'How does social media affect people''s self-esteem and mental health?'),
('speaking', NULL, 3, 'part3', 'Public Transport vs Roads',
 'Should cities invest more in public transportation or in roads for cars? Why?'),
('speaking', NULL, 4, 'part3', 'Changing Family Structures',
 'How has the concept of family changed in recent decades across different cultures?'),
('speaking', NULL, 5, 'part3', 'Modern Life Stress',
 'What are the main causes of stress in modern life and how can they be reduced?'),

-- ========================================
-- WRITING TASK 1 — GENERAL TRAINING (5 letters, difficulty 2-3)
-- ========================================
('writing', 'task1', 2, 'general', 'Letter — New City',
 'You recently moved to a new city for work. Write a letter to a friend. In your letter:
- describe your new city and what it is like
- explain what your new job involves
- invite them to visit you'),
('writing', 'task1', 2, 'general', 'Letter — Damaged Item',
 'You purchased an item online that arrived damaged. Write a letter to the company. In your letter:
- describe what you ordered
- explain what the damage is
- state what you expect the company to do'),
('writing', 'task1', 2, 'general', 'Letter — Library Hours',
 'Your local library is planning to reduce its opening hours. Write a letter to the library manager. In your letter:
- express your concern about the planned changes
- explain how you and others use the library
- suggest alternative ways to save costs'),
('writing', 'task1', 3, 'general', 'Letter — Course Thank You',
 'You attended a training course and found it very beneficial. Write a letter to the organizer. In your letter:
- thank them for the course
- explain which parts were most useful to you
- suggest how the course could be improved'),
('writing', 'task1', 2, 'general', 'Letter — Noisy Neighbor',
 'Your neighbor''s dog has been barking loudly at night, disturbing your sleep. Write a letter to your neighbor. In your letter:
- explain the problem clearly
- describe how it is affecting you
- suggest a way to resolve the issue'),

-- ========================================
-- WRITING TASK 1 — ACADEMIC (5 data/process, difficulty 2-4)
-- ========================================
('writing', 'task1', 2, 'academic', 'Internet Access Chart',
 'The chart below shows the percentage of households in a country with internet access from 2010 to 2020. Summarize the information by selecting and reporting the main features, and make comparisons where relevant.'),
('writing', 'task1', 3, 'academic', 'Plastic Recycling Process',
 'The diagram illustrates the process of recycling plastic bottles. Summarize the information by selecting and reporting the main features, and make comparisons where relevant.'),
('writing', 'task1', 2, 'academic', 'Monthly Rainfall Table',
 'The table below shows the average monthly rainfall in three different cities. Summarize the information by selecting and reporting the main features, and make comparisons where relevant.'),
('writing', 'task1', 3, 'academic', 'Town Center Map',
 'The map shows changes to a town center between 2005 and the present day. Summarize the information by selecting and reporting the main features, and make comparisons where relevant.'),
('writing', 'task1', 3, 'academic', 'Tourist Numbers Bar Chart',
 'The bar chart compares the number of tourists visiting four different countries in 2010 and 2020. Summarize the information by selecting and reporting the main features, and make comparisons where relevant.'),

-- ========================================
-- WRITING TASK 2 (15 essays, difficulty 3-5)
-- ========================================
('writing', 'task2', 3, 'general', 'Social Media Impact',
 'Some people believe that social media has a negative impact on society. To what extent do you agree or disagree?'),
('writing', 'task2', 3, 'general', 'Aging Population',
 'Many countries are experiencing an increase in life expectancy. What are the benefits and drawbacks of an aging population?'),
('writing', 'task2', 4, 'general', 'Community Service in Schools',
 'Some people think that unpaid community service should be a compulsory part of high school education. Do you agree or disagree?'),
('writing', 'task2', 3, 'general', 'Cycling Popularity',
 'In many cities, the use of bicycles as a form of transport is becoming more popular. Why do you think this is happening, and what are the benefits of this trend?'),
('writing', 'task2', 4, 'general', 'Public Health Approaches',
 'Some people believe that the best way to improve public health is by increasing the number of sports facilities. Others believe there are more effective methods. Discuss both views and give your opinion.'),
('writing', 'task2', 3, 'academic', 'Remote Work Pros and Cons',
 'Many people today are choosing to work remotely rather than in a traditional office. What are the advantages and disadvantages of this trend?'),
('writing', 'task2', 4, 'academic', 'Transport Investment Debate',
 'Some people think that governments should invest more in public transportation to reduce traffic congestion. Others believe building more roads is the solution. Discuss both views and give your opinion.'),
('writing', 'task2', 3, 'academic', 'Museum Attendance Decline',
 'In some countries, the number of people visiting museums and art galleries is declining. What are the causes of this trend, and how can it be addressed?'),
('writing', 'task2', 4, 'general', 'Purpose of Advertising',
 'Some people argue that the primary purpose of advertising is to increase sales rather than to inform consumers. To what extent do you agree or disagree?'),
('writing', 'task2', 4, 'academic', 'Later Parenthood',
 'An increasing number of people are choosing to have children later in life. What are the reasons for this trend, and how does it affect family dynamics?'),
('writing', 'task2', 5, 'academic', 'Climate vs Economy',
 'Some people believe that climate change is the most pressing issue facing humanity today. Others argue that economic development should take priority. Discuss both views and give your opinion.'),
('writing', 'task2', 3, 'general', 'Gap Year Benefits',
 'Many young people are choosing to take a gap year between finishing school and starting university. What are the benefits and potential drawbacks of this decision?'),
('writing', 'task2', 4, 'academic', 'Free University Education',
 'Some people think that the government should provide free university education to all citizens. Others believe that students should bear the cost of their own education. Discuss both views and give your opinion.'),
('writing', 'task2', 4, 'general', 'Elderly Living Arrangements',
 'In many cultures, it is traditional for elderly parents to live with their adult children. In other cultures, older people often live in retirement homes. What are the advantages and disadvantages of each approach?'),
('writing', 'task2', 5, 'academic', 'Space Exploration Debate',
 'Some people believe that space exploration is a waste of resources that could be better used to address problems on Earth. To what extent do you agree or disagree?');
