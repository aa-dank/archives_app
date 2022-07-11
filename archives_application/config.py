import os

DIRECTORY_CHOICES = ['A - General', 'B - Administrative Reviews and Approvals', 'C - Consultants',
                     'D - Environmental Review Process', 'E - Program and Design',
                     'F - Bid Documents and Contract Award', 'G - Construction', "H - Submittals and O&M's",
                     'A1 - Miscellaneous', 'A2 - Working File', 'A3 - Project Directory Matrix & Project Chronology',
                     "B1 - CPS and Chancellor's Approvals", 'B11 - LEED', 'B12 - Outside Regulatory Agencies',
                     'B13 - Coastal Commission', 'B2 - Office of the President UC Regents',
                     'B3 - State Public Works Board', 'B4 - Department of Finance', 'B5 - Legislative Submittals',
                     'B6 - State Fire Marshal', 'B7 - Office of State Architect  (DSA)', 'B8 -  General Counsel',
                     'B8.1 - General Counsel - Confidential', 'C1 - Executive Architect', 'C1.1 - Selection',
                     'C1.2 - Correspondence', 'C1.3 - Agreement', 'C2 - Other Consultants', 'C2.1 - Selection',
                     'C2.2 - Correspondence', 'C2.3 - Agreement', 'D1 - Environmental Correspondences',
                     'D2 - EIC Forms', 'D3 - CEQA Documentation', 'D4 - Mitigation Monitoring Program', 'E1 - DPP',
                     'E2 - PPG', 'E3 - Budget Cost Estimates', 'E4 - Planning Schedules',
                     'E5 - Program and Design Correspondences', 'E5.1 - Executive Architect Correspondences',
                     'E5.2 - Special Consultants', 'E5.3 - Users. Building Committee. Campus Correspondences',
                     'E5.4 - PPC and PP', 'E5.5 - Office of the President to.from', 'E5.6 - Building Committee to.from',
                     'E5.7 - Other', 'E5.8 - Office of General Counsel', 'E6 - Reports (soils, structural, calcs)',
                     'E7 - Value Engineering', 'E7.1 - Value Engineering Correspondence',
                     'E7.2 - VE Workshop Minutes, Summaries, Final Reports', 'E8 - Program and Design Meeting Minutes',
                     'F1 - Bid and Contract Award Correspondence', 'F1.1 - Executive Architect Correspondences',
                     'F1.2 - Special Consultants Correspondences', 'F1.4 - PPC and PP',
                     'F1.5 - Office of the President Correspondences', 'F1.6 - General Counsel Correspondences',
                     'F1.7 - Pre-Qualification', 'F1.8 - Other', 'F10 - Escrow Agreement',
                     'F2 - Reviews', 'F2.1 - Constructibility, Code Reviews', 'F2.2 - In-house. PP reviews',
                     'F3 - Structural, Title 24, Mech Calculations', 'F4 - Plan Deposits, Planholders, Ads for Bid',
                     'F2.3 - Independent Cost Review', 'F2.4 - Independent Seismic Review', 'F2.5 - Other',
                     'F5 - Drawings and Spec', 'F6 - Affirmative Action', 'F7 - Bid Summary Forms',
                     'F7.1 - Bid Protest', 'F8 - Contract', 'F9 - Builders Risk Insurance',
                     'G1 - Construction Correspondence', 'G1.1 - Contractor Correspondences',
                     'G1.2 - Executive Architect Correspondences',
                     'G1.3 - Users.Building Committee.Campus Correspondences', 'G1.4 - PPC and PP. Certified Payroll',
                     'G1.5 - Geotechnical Engineer Correspondences',
                     'G1.6 - Testing and Inspection to Laboratory Correspondences',
                     'G1.7 - General Counsel Correspondences', 'G1.8 - Other',
                     'G10 - Testing and Inspection Reports.Other',
                     'G11 - Proposal Requests. Bulletins. Contractors Response', 'G12 - Request for Information RFI',
                     'G13 - Letter of Instruction LOI', 'G14 - User Request Change in Scope', 'G15 - Change Order',
                     'G16 - Field Orders', 'G17 - Warranties and Guarantees', 'G18 - Punchlist',
                     'G19 - Notice of Completion', 'G2 - Certificate of Payment', 'G20 - Warranty Deficiency',
                     'G21 - Construction Photos', 'G22 - Claims. Public Records Act', 'G22.1 - Claims Confidential',
                     'G23 - Commissioning', 'G24 - Building Permits', "G3 - Contractor's Schedule and Updates",
                     'G4 - Progress Meeting Notes', 'G5 - UCSC Inspectors Daily Reports', 'G5.1 - Hot Work Permits',
                     'G6 - UCSC Memoranda', 'G6.1 - Architects Field Reports', 'G7 - Contractors Daily Reports',
                     'G8 - Testing and Inspection Reports. Geotechnical Engineer',
                     'G9 - Testing and Inspection Reports. Testing Laboratory']

RECORDS_SERVER_LOCATION = r"""\\ppcou.ucsc.edu\Data\PPC_Records"""

INBOXES_LOCATION = r"""\\ppcou.ucsc.edu\Data\Cannon_Scans\INBOX"""

DEFAULT_DATETIME_FORMAT = "%m/%d/%Y, %H:%M:%S"

ROLES = ['ADMIN', 'ARCHIVIST', 'STAFF']

class DefaultConfig:
    SQLALCHEMY_DATABASE_URI = 'sqlite////ppcou.ucsc.edu/Data/Archive_Data/archives_app.db'
    SECRET_KEY = 'ABC'
