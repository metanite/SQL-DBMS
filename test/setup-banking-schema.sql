-- ============================================
-- Clean setup: Banking schema
-- Tables: branch, account, customer, loan, borrower
-- ============================================

-- Drop existing tables (reverse dependency order)
drop table borrower;
drop table loan;
drop table account;
drop table customer;
drop table branch;

-- Create tables
create table branch (
    branch_name char(15) not null,
    branch_city char(15),
    assets int,
    primary key (branch_name)
);

create table account (
    account_number int not null,
    branch_name char(15),
    balance int,
    primary key (account_number),
    foreign key (branch_name) references branch(branch_name)
);

create table customer (
    customer_name char(20) not null,
    customer_street char(20),
    customer_city char(20),
    primary key (customer_name)
);

create table loan (
    loan_number int not null,
    branch_name char(15),
    amount int,
    primary key (loan_number),
    foreign key (branch_name) references branch(branch_name)
);

create table borrower (
    customer_name char(20) not null,
    loan_number int not null,
    primary key (customer_name, loan_number),
    foreign key (customer_name) references customer(customer_name),
    foreign key (loan_number) references loan(loan_number)
);

-- Insert branches
insert into branch values('Brighton', 'Brooklyn', 7100000);
insert into branch values('Downtown', 'Brooklyn', 9000000);
insert into branch values('Mianus', 'Horseneck', 400000);
insert into branch values('North Town', 'Rye', 3700000);
insert into branch values('Perryridge', 'Horseneck', 1700000);
insert into branch values('Pownal', 'Bennington', 300000);
insert into branch values('Redwood', 'Palo Alto', 2100000);
insert into branch values('Round Hill', 'Horseneck', 8000000);

-- Insert accounts
insert into account values(101, 'Downtown', 500);
insert into account values(102, 'Perryridge', 400);
insert into account values(201, 'Brighton', 900);
insert into account values(215, 'Mianus', 700);
insert into account values(217, 'Brighton', 750);
insert into account values(222, 'Redwood', 700);
insert into account values(305, 'Round Hill', 350);

-- Insert customers
insert into customer values('Adams', 'Spring', 'Pittsfield');
insert into customer values('Brooks', 'Senator', 'Brooklyn');
insert into customer values('Curry', 'North', 'Rye');
insert into customer values('Glenn', 'Sand Hill', 'Woodside');
insert into customer values('Green', 'Walnut', 'Stamford');
insert into customer values('Hayes', 'Main', 'Harrison');
insert into customer values('Johnson', 'Alma', 'Palo Alto');
insert into customer values('Jones', 'Main', 'Harrison');
insert into customer values('Lindsay', 'Park', 'Pittsfield');
insert into customer values('Smith', 'North', 'Rye');
insert into customer values('Turner', 'Putnam', 'Stamford');
insert into customer values('Williams', 'Nassau', 'Princeton');

-- Insert loans
insert into loan values(11, 'Round Hill', 900);
insert into loan values(14, 'Downtown', 1500);
insert into loan values(15, 'Perryridge', 1500);
insert into loan values(16, 'Perryridge', 1300);
insert into loan values(17, 'Downtown', 1000);
insert into loan values(23, 'Redwood', 2000);
insert into loan values(93, 'Mianus', 500);

-- Insert borrowers
insert into borrower values('Adams', 16);
insert into borrower values('Curry', 93);
insert into borrower values('Hayes', 15);
insert into borrower values('Jackson', 14);
insert into borrower values('Jones', 17);
insert into borrower values('Smith', 11);
insert into borrower values('Smith', 23);
insert into borrower values('Williams', 17);

show tables;

select * from branch;
select * from account;
select * from customer;
select * from loan;
select * from borrower;
