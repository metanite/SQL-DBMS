-- ============================================
-- Clean setup: University schema
-- Tables: department, student, instructor, advisor
-- ============================================

-- Drop existing tables (reverse dependency order)
drop table advisor;
drop table instructor;
drop table student;
drop table department;

-- Create tables
create table department (
    dept_name char(20) not null,
    building char(20),
    budget int,
    primary key (dept_name)
);

create table student (
    ID char(5) not null,
    name char(20) not null,
    dept_name char(20) not null,
    primary key (ID),
    foreign key (dept_name) references department(dept_name)
);

create table instructor (
    id int not null,
    name char(20),
    dept_name char(20),
    primary key (id),
    foreign key (dept_name) references department(dept_name)
);

create table advisor (
    s_id char(5) not null,
    i_id char(5) not null,
    primary key (s_id, i_id),
    foreign key (s_id) references student(ID)
);

-- Insert departments
insert into department values('Comp. Sci.', 'Taylor', 100000);
insert into department values('Biology', 'Watson', 90000);
insert into department values('Elec. Eng.', 'Taylor', 85000);
insert into department values('Music', 'Packard', 80000);
insert into department values('Finance', 'Painter', 120000);
insert into department values('History', 'Painter', 50000);
insert into department values('Physics', 'Watson', 70000);

-- Insert students
insert into student values('00128', 'Zhang', 'Comp. Sci.');
insert into student values('12345', 'Shankar', 'Comp. Sci.');
insert into student values('19991', 'Brandt', 'History');
insert into student values('23121', 'Chavez', 'Finance');
insert into student values('44553', 'Peltier', 'Physics');
insert into student values('45678', 'Levy', 'Physics');
insert into student values('54321', 'Williams', 'Comp. Sci.');
insert into student values('55739', 'Sanchez', 'Music');
insert into student values('70557', 'Snow', 'Physics');
insert into student values('76543', 'Brown', 'Comp. Sci.');
insert into student values('76653', 'Aoi', 'Elec. Eng.');
insert into student values('98765', 'Bourikas', 'Elec. Eng.');
insert into student values('98988', 'Tanaka', 'Biology');

-- Insert instructors
insert into instructor values(10101, 'Srinivasan', 'Comp. Sci.');
insert into instructor values(12121, 'Wu', 'Finance');
insert into instructor values(15151, 'Mozart', 'Music');
insert into instructor values(22222, 'Einstein', 'Physics');
insert into instructor values(32343, 'El Said', 'History');
insert into instructor values(33456, 'Gold', 'Physics');
insert into instructor values(45565, 'Katz', 'Comp. Sci.');
insert into instructor values(58583, 'Califieri', 'History');
insert into instructor values(76543, 'Singh', 'Finance');
insert into instructor values(76766, 'Crick', 'Biology');
insert into instructor values(83821, 'Brandt', 'Comp. Sci.');
insert into instructor values(98345, 'Kim', 'Elec. Eng.');

-- Insert advisors
insert into advisor values('00128', '45565');
insert into advisor values('12345', '10101');
insert into advisor values('23121', '76543');
insert into advisor values('44553', '22222');
insert into advisor values('45678', '22222');
insert into advisor values('76543', '45565');
insert into advisor values('76653', '98345');
insert into advisor values('98765', '98345');
insert into advisor values('98988', '76766');

show tables;

select * from department;
select * from student;
select * from instructor;
select * from advisor;
