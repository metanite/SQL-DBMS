-- ============================================
-- Clean setup: Student / Lecture schema
-- Run this to create and populate the tables
-- used in the 1-3 test suite.
-- ============================================

-- Drop existing tables (reverse dependency order)
drop table apply;
drop table ref;
drop table lectures;
drop table students;

-- Create tables
create table students (
    id char(10) not null,
    name char(20),
    primary key (id)
);

create table lectures (
    id int not null,
    name char(20),
    capacity int,
    primary key (id)
);

create table ref (
    id int,
    foreign key (id) references lectures (id)
);

create table apply (
    s_id char(10) not null,
    l_id int not null,
    apply_date date,
    primary key (s_id, l_id),
    foreign key (s_id) references students (id),
    foreign key (l_id) references lectures (id)
);

-- Insert students
insert into students (id, name) values('S001', 'John Doe');
insert into students (id, name) values('S002', 'Jane Smith');
insert into students (id, name) values('S003', 'Michael Johnson');
insert into students (id, name) values('S004', 'Emily Davis');
insert into students (id, name) values('S005', 'David Miller');
insert into students (id, name) values('S006', 'Sophia Garcia');
insert into students values('S007', 'Sue Park');
insert into students values('S008', null);
insert into students values('S009', 'Sean Park');
insert into students values('S010', null);

-- Insert lectures
insert into lectures (id, name, capacity) values(1, 'Maths 101', 30);
insert into lectures (id, name, capacity) values(2, 'Physics 101', 25);
insert into lectures (id, name, capacity) values(3, 'Chemistry 101', 30);
insert into lectures (id, name, capacity) values(4, 'Biology 101', 35);
insert into lectures (id, name, capacity) values(5, 'English 101', 40);
insert into lectures (id, name, capacity) values(6, 'History 101', 45);
insert into lectures values(7, null, null);
insert into lectures values(8, null, 5);
insert into lectures values(9, 'CS 101', null);

-- Insert refs
insert into ref (id) values(1);
insert into ref (id) values(2);
insert into ref (id) values(3);
insert into ref (id) values(4);
insert into ref (id) values(5);
insert into ref (id) values(6);

-- Insert applies
insert into apply (s_id, l_id, apply_date) values('S001', 1, '2023-05-16');
insert into apply (s_id, l_id, apply_date) values('S002', 2, '2023-05-17');
insert into apply (s_id, l_id, apply_date) values('S003', 3, '2023-05-18');
insert into apply (s_id, l_id, apply_date) values('S004', 4, '2023-05-19');
insert into apply (s_id, l_id, apply_date) values('S005', 5, '2023-05-20');
insert into apply (s_id, l_id, apply_date) values('S006', 6, '2023-05-21');
insert into apply values('S007', 7, null);

show tables;

select * from students;
select * from lectures;
select * from ref;
select * from apply;
