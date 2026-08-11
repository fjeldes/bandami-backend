-- Fix speaking questions with incorrect module values
-- Speaking questions should have module = 'part1', 'part2', or 'part3'
-- This migration corrects any speaking questions that have invalid module values (e.g., 'general')

UPDATE question_bank 
SET module = 'part1' 
WHERE exam_type = 'speaking' 
  AND module NOT IN ('part1', 'part2', 'part3');

-- Fix writing questions with incorrect module values
-- Writing questions should have module = 'general' or 'academic'
-- This migration corrects any writing questions that have invalid module values

UPDATE question_bank 
SET module = 'general' 
WHERE exam_type = 'writing' 
  AND module NOT IN ('general', 'academic');

-- Fix speaking questions with incorrect task_type values
-- Speaking questions should have task_type = NULL
-- This migration corrects any speaking questions that have invalid task_type values

UPDATE question_bank 
SET task_type = NULL 
WHERE exam_type = 'speaking' 
  AND task_type IS NOT NULL;

-- Fix writing questions with incorrect task_type values
-- Writing questions should have task_type = 'task1' or 'task2'
-- This migration corrects any writing questions that have invalid task_type values

UPDATE question_bank 
SET task_type = 'task1' 
WHERE exam_type = 'writing' 
  AND task_type NOT IN ('task1', 'task2');
