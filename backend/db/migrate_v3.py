"""Migration v3 — schema updates from the big honkin refactor.
                                                                                                                                    
Changes applied (all idempotent / safe to re-run):        
                                                                                                                                    
1. enrollment_assets table                                                                                                         
2. course_decomposition_jobs:
    - rename user_id → creator_id  (requires SQLite 3.25+)                                                                          
    - add decompose_mode column                            
                                                                                                                                    
Run:                                                      
    uv run python -m backend.db.migrate_v3
"""                                                                                                                                

from __future__ import annotations                                                                                                 
                                                        
import sqlite3
import sys
from pathlib import Path

_ENROLLMENT_ASSETS_DDL = """                                                                                                       
CREATE TABLE IF NOT EXISTS enrollment_assets (
    id            TEXT PRIMARY KEY,                                                                                                
    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,                                               
    section_idx   INTEGER NOT NULL,                                                                                                
    asset_type    TEXT NOT NULL DEFAULT 'ai_image',                                                                                
    image_path    TEXT,                                                                                                            
    prompt        TEXT,                                                                                                            
    revised_prompt TEXT,                                                                                                           
    tool_use_id   TEXT,                                                                                                            
    idx           INTEGER NOT NULL DEFAULT 0,                                                                                      
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))                                                                          
);                                                                                                                                 
CREATE INDEX IF NOT EXISTS idx_enrollment_assets_enrollment                                                                        
    ON enrollment_assets(enrollment_id, section_idx);                                                                              
"""                                                       
                                                                                                                                    
                                                        
def _col_names(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}                                                                                      

                                                                                                                                    
def run(db_path: Path) -> None:                           
    print(f"[migrate_v3] connecting to {db_path}")
    con = sqlite3.connect(db_path)                                                                                                 

    # 1. enrollment_assets                                                                                                         
    con.executescript(_ENROLLMENT_ASSETS_DDL)             
    print("[migrate_v3] enrollment_assets — ok")                                                                                   
                                                                                                                                    
    # 2. course_decomposition_jobs: rename user_id → creator_id                                                                    
    cols = _col_names(con, "course_decomposition_jobs")                                                                            
    if "user_id" in cols and "creator_id" not in cols:                                                                             
        con.execute(                                                                                                               
            "ALTER TABLE course_decomposition_jobs RENAME COLUMN user_id TO creator_id"
        )                                                                                                                          
        print("[migrate_v3] course_decomposition_jobs: renamed user_id → creator_id")
    else:                                                                                                                          
        print("[migrate_v3] course_decomposition_jobs: user_id rename — skipped")
                                                                                                                                    
    # 3. course_decomposition_jobs: add decompose_mode if missing                                                                  
    cols = _col_names(con, "course_decomposition_jobs")                                                                            
    if "decompose_mode" not in cols:                                                                                               
        con.execute(                                      
            "ALTER TABLE course_decomposition_jobs"
            " ADD COLUMN decompose_mode TEXT NOT NULL DEFAULT 'pdf'"                                                               
        )                                                                                                                          
        print("[migrate_v3] course_decomposition_jobs: added decompose_mode column")                                               
    else:                                                                                                                          
        print("[migrate_v3] course_decomposition_jobs: decompose_mode — skipped")                                                  
                                                                                                                                    
    con.commit()
    con.close()                                                                                                                    
    print("[migrate_v3] done.")                           


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])                                                                                                   
    else:
        from ..config import settings  # type: ignore[import]                                                                      
        path = settings.DB_PATH                                                                                                    
    run(path)
