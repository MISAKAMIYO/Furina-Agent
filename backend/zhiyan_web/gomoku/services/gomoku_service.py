"""
五子棋服务层
提供人机对战五子棋的核心业务逻辑

功能:
- 创建新游戏
- 玩家落子
- AI落子
- 判断胜负
- 获取游戏状态
"""
from __future__ import annotations

import os
import json
import uuid
import logging
from datetime import datetime

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID
BOARD_SIZE = 15  # 棋盘大小


def init_gomoku_db():
    """
    初始化五子棋数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建五子棋游戏表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gomoku_games (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                board TEXT NOT NULL DEFAULT '[]',
                current_player TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'playing',
                winner TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info("五子棋数据库初始化完成")
    except Exception as e:
        logger.error(f"五子棋数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def create_new_game(user_id: str):
    """
    创建新游戏
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 游戏信息
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        game_id = str(uuid.uuid4())
        now = int(datetime.now().timestamp())
        
        # 创建空棋盘
        board = [[0 for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        
        cursor.execute(
            'INSERT INTO gomoku_games (id, user_id, board, current_player, status, winner, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (game_id, user_id, json.dumps(board), 'user', 'playing', None, now, now)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "游戏创建成功！你先手",
            "data": {
                "game_id": game_id,
                "board": board,
                "current_player": "user",
                "status": "playing",
            }
        }
    except Exception as e:
        logger.error(f"创建五子棋游戏失败: {e}")
        return {"success": False, "message": "创建游戏失败"}
    finally:
        conn.close()


def get_game_status(game_id: str):
    """
    获取游戏状态
    
    参数:
        game_id: 游戏ID
    
    返回:
        dict: 游戏状态信息
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM gomoku_games WHERE id = ?', (game_id,))
        game = cursor.fetchone()
        
        if not game:
            return {"success": False, "message": "游戏不存在"}
        
        return {
            "success": True,
            "data": {
                "game_id": game["id"],
                "board": json.loads(game["board"]),
                "current_player": game["current_player"],
                "status": game["status"],
                "winner": game["winner"],
                "created_at": game["created_at"],
                "updated_at": game["updated_at"],
            }
        }
    except Exception as e:
        logger.error(f"获取游戏状态失败: {e}")
        return {"success": False, "message": "获取游戏状态失败"}
    finally:
        conn.close()


def make_move(game_id: str, user_id: str, row: int, col: int):
    """
    玩家落子
    
    参数:
        game_id: 游戏ID
        user_id: 用户ID
        row: 行坐标
        col: 列坐标
    
    返回:
        dict: 落子结果
    """
    if row < 0 or row >= BOARD_SIZE or col < 0 or col >= BOARD_SIZE:
        return {"success": False, "message": "坐标超出棋盘范围"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM gomoku_games WHERE id = ? AND user_id = ?', (game_id, user_id))
        game = cursor.fetchone()
        
        if not game:
            return {"success": False, "message": "游戏不存在"}
        
        if game["status"] != "playing":
            return {"success": False, "message": "游戏已结束"}
        
        if game["current_player"] != "user":
            return {"success": False, "message": "当前不是你的回合"}
        
        board = json.loads(game["board"])
        
        if board[row][col] != 0:
            return {"success": False, "message": "该位置已有棋子"}
        
        # 玩家落子（黑棋）
        board[row][col] = 1
        
        # 检查胜负
        if _check_win(board, row, col, 1):
            cursor.execute(
                'UPDATE gomoku_games SET board = ?, status = ?, winner = ?, updated_at = ? WHERE id = ?',
                (json.dumps(board), 'ended', 'user', int(datetime.now().timestamp()), game_id)
            )
            conn.commit()
            
            return {
                "success": True,
                "message": "恭喜！你赢了！",
                "data": {
                    "game_id": game_id,
                    "board": board,
                    "current_player": "user",
                    "status": "ended",
                    "winner": "user",
                }
            }
        
        # 检查平局
        if _check_draw(board):
            cursor.execute(
                'UPDATE gomoku_games SET board = ?, status = ?, winner = ?, updated_at = ? WHERE id = ?',
                (json.dumps(board), 'ended', 'draw', int(datetime.now().timestamp()), game_id)
            )
            conn.commit()
            
            return {
                "success": True,
                "message": "平局！",
                "data": {
                    "game_id": game_id,
                    "board": board,
                    "current_player": "user",
                    "status": "ended",
                    "winner": "draw",
                }
            }
        
        # AI落子
        ai_row, ai_col = _ai_move(board)
        board[ai_row][ai_col] = 2
        
        # 检查AI是否获胜
        if _check_win(board, ai_row, ai_col, 2):
            cursor.execute(
                'UPDATE gomoku_games SET board = ?, status = ?, winner = ?, updated_at = ? WHERE id = ?',
                (json.dumps(board), 'ended', 'ai', int(datetime.now().timestamp()), game_id)
            )
            conn.commit()
            
            return {
                "success": True,
                "message": "很遗憾，AI赢了！",
                "data": {
                    "game_id": game_id,
                    "board": board,
                    "current_player": "user",
                    "status": "ended",
                    "winner": "ai",
                    "ai_move": {"row": ai_row, "col": ai_col},
                }
            }
        
        # 检查平局
        if _check_draw(board):
            cursor.execute(
                'UPDATE gomoku_games SET board = ?, status = ?, winner = ?, updated_at = ? WHERE id = ?',
                (json.dumps(board), 'ended', 'draw', int(datetime.now().timestamp()), game_id)
            )
            conn.commit()
            
            return {
                "success": True,
                "message": "平局！",
                "data": {
                    "game_id": game_id,
                    "board": board,
                    "current_player": "user",
                    "status": "ended",
                    "winner": "draw",
                    "ai_move": {"row": ai_row, "col": ai_col},
                }
            }
        
        # 更新游戏状态
        cursor.execute(
            'UPDATE gomoku_games SET board = ?, updated_at = ? WHERE id = ?',
            (json.dumps(board), int(datetime.now().timestamp()), game_id)
        )
        conn.commit()
        
        return {
            "success": True,
            "message": "落子成功",
            "data": {
                "game_id": game_id,
                "board": board,
                "current_player": "user",
                "status": "playing",
                "ai_move": {"row": ai_row, "col": ai_col},
            }
        }
    except Exception as e:
        logger.error(f"落子失败: {e}")
        return {"success": False, "message": "落子失败"}
    finally:
        conn.close()


def cancel_game(game_id: str, user_id: str):
    """
    取消游戏
    
    参数:
        game_id: 游戏ID
        user_id: 用户ID
    
    返回:
        dict: 取消结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM gomoku_games WHERE id = ? AND user_id = ?', (game_id, user_id))
        conn.commit()
        
        if cursor.rowcount > 0:
            return {"success": True, "message": "游戏已取消"}
        else:
            return {"success": False, "message": "游戏不存在"}
    except Exception as e:
        logger.error(f"取消游戏失败: {e}")
        return {"success": False, "message": "取消游戏失败"}
    finally:
        conn.close()


def _check_win(board: list, row: int, col: int, player: int) -> bool:
    """
    检查是否获胜
    
    参数:
        board: 棋盘
        row: 落子行
        col: 落子列
        player: 玩家（1=黑棋，2=白棋）
    
    返回:
        bool: 是否获胜
    """
    directions = [
        (0, 1),   # 水平
        (1, 0),   # 垂直
        (1, 1),   # 对角线
        (1, -1),  # 反对角线
    ]
    
    for dx, dy in directions:
        count = 1
        
        # 正向检查
        r, c = row + dx, col + dy
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == player:
            count += 1
            r += dx
            c += dy
        
        # 反向检查
        r, c = row - dx, col - dy
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == player:
            count += 1
            r -= dx
            c -= dy
        
        if count >= 5:
            return True
    
    return False


def _check_draw(board: list) -> bool:
    """
    检查是否平局
    
    参数:
        board: 棋盘
    
    返回:
        bool: 是否平局
    """
    for row in board:
        if 0 in row:
            return False
    return True


def _ai_move(board: list) -> tuple:
    """
    AI落子逻辑（简单策略）
    
    参数:
        board: 棋盘
    
    返回:
        tuple: (row, col) 落子位置
    """
    import random
    
    # 评估函数权重
    weights = {
        4: 10000,  # 四连
        3: 1000,   # 三连
        2: 100,    # 两连
        1: 10,     # 一连
    }
    
    best_score = -1
    best_moves = []
    
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            if board[row][col] == 0:
                # 检查周围是否有棋子
                has_neighbor = False
                for dr in [-2, -1, 0, 1, 2]:
                    for dc in [-2, -1, 0, 1, 2]:
                        nr, nc = row + dr, col + dc
                        if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE and board[nr][nc] != 0:
                            has_neighbor = True
                            break
                    if has_neighbor:
                        break
                
                if not has_neighbor and (row != BOARD_SIZE // 2 or col != BOARD_SIZE // 2):
                    continue
                
                # 评估该位置
                score = _evaluate_position(board, row, col, 2)  # AI是白棋
                
                # 检查是否需要阻止玩家
                player_score = _evaluate_position(board, row, col, 1)  # 玩家是黑棋
                
                # 优先阻止玩家
                if player_score >= weights[4]:
                    return (row, col)
                if player_score >= weights[3]:
                    score += player_score * 2
                
                if score > best_score:
                    best_score = score
                    best_moves = [(row, col)]
                elif score == best_score:
                    best_moves.append((row, col))
    
    if best_moves:
        return random.choice(best_moves)
    
    # 如果没有好的位置，返回中心
    return (BOARD_SIZE // 2, BOARD_SIZE // 2)


def _evaluate_position(board: list, row: int, col: int, player: int) -> int:
    """
    评估位置分数
    
    参数:
        board: 棋盘
        row: 行
        col: 列
        player: 玩家
    
    返回:
        int: 分数
    """
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    total_score = 0
    
    for dx, dy in directions:
        count = 1
        open_ends = 0
        
        # 正向检查
        r, c = row + dx, col + dy
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
            if board[r][c] == player:
                count += 1
                r += dx
                c += dy
            elif board[r][c] == 0:
                open_ends += 1
                break
            else:
                break
        
        # 反向检查
        r, c = row - dx, col - dy
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
            if board[r][c] == player:
                count += 1
                r -= dx
                c -= dy
            elif board[r][c] == 0:
                open_ends += 1
                break
            else:
                break
        
        # 根据连续数量和开放端计算分数
        if count >= 5:
            total_score += 100000
        elif count == 4:
            if open_ends >= 1:
                total_score += 10000
            else:
                total_score += 1000
        elif count == 3:
            if open_ends >= 2:
                total_score += 1000
            elif open_ends >= 1:
                total_score += 100
        elif count == 2:
            if open_ends >= 2:
                total_score += 100
            elif open_ends >= 1:
                total_score += 10
        elif count == 1:
            if open_ends >= 2:
                total_score += 10
    
    return total_score
