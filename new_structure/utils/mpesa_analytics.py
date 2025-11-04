"""
M-PESA Analytics Service
Provides statistics and insights on M-PESA transactions.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, extract, case
from ..models.fee_management import MpesaTransaction, Payment
from ..extensions import db

logger = logging.getLogger(__name__)


class MpesaAnalytics:
    """Service for M-PESA payment analytics and statistics."""
    
    @staticmethod
    def get_dashboard_stats(days=30):
        """
        Get comprehensive M-PESA dashboard statistics.
        
        Args:
            days: Number of days to include in analysis (default: 30)
            
        Returns:
            Dictionary with various statistics and trends
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Basic transaction counts
            total_transactions = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date
            ).count()
            
            successful = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).count()
            
            failed = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'failed'
            ).count()
            
            pending = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'pending'
            ).count()
            
            timed_out = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'timeout'
            ).count()
            
            cancelled = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'cancelled'
            ).count()
            
            # Financial statistics
            total_amount_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            total_amount = float(total_amount_result or 0)
            
            # Average transaction amount
            avg_amount_result = db.session.query(
                func.avg(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            avg_amount = float(avg_amount_result or 0)
            
            # Success rate
            success_rate = (successful / total_transactions * 100) if total_transactions > 0 else 0
            
            # Today's statistics
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_transactions = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= today_start
            ).count()
            
            today_successful = MpesaTransaction.query.filter(
                MpesaTransaction.created_at >= today_start,
                MpesaTransaction.status == 'success'
            ).count()
            
            today_amount_result = db.session.query(
                func.sum(MpesaTransaction.amount)
            ).filter(
                MpesaTransaction.created_at >= today_start,
                MpesaTransaction.status == 'success'
            ).scalar()
            
            today_amount = float(today_amount_result or 0)
            
            return {
                'period_days': days,
                'total_transactions': total_transactions,
                'successful': successful,
                'failed': failed,
                'pending': pending,
                'timed_out': timed_out,
                'cancelled': cancelled,
                'success_rate': round(success_rate, 2),
                'total_amount': round(total_amount, 2),
                'avg_amount': round(avg_amount, 2),
                'today': {
                    'transactions': today_transactions,
                    'successful': today_successful,
                    'amount': round(today_amount, 2)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
            return {
                'error': str(e),
                'total_transactions': 0,
                'successful': 0,
                'failed': 0,
                'pending': 0,
                'success_rate': 0,
                'total_amount': 0,
                'avg_amount': 0
            }
    
    @staticmethod
    def get_daily_trends(days=30):
        """
        Get daily transaction trends.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            List of daily statistics
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Query daily aggregates
            daily_data = db.session.query(
                func.date(MpesaTransaction.created_at).label('date'),
                func.count(MpesaTransaction.id).label('count'),
                func.sum(
                    case(
                        (MpesaTransaction.status == 'success', MpesaTransaction.amount),
                        else_=0
                    )
                ).label('total_amount'),
                func.sum(
                    case((MpesaTransaction.status == 'success', 1), else_=0)
                ).label('successful'),
                func.sum(
                    case((MpesaTransaction.status == 'failed', 1), else_=0)
                ).label('failed')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date
            ).group_by(
                func.date(MpesaTransaction.created_at)
            ).order_by(
                func.date(MpesaTransaction.created_at)
            ).all()
            
            # Format results
            trends = []
            for row in daily_data:
                trends.append({
                    'date': row.date.isoformat() if row.date else None,
                    'count': row.count,
                    'amount': float(row.total_amount or 0),
                    'successful': row.successful,
                    'failed': row.failed
                })
            
            return trends
            
        except Exception as e:
            logger.error(f"Error getting daily trends: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_hourly_distribution():
        """
        Get hourly distribution of transactions (which hours have most payments).
        
        Returns:
            List of hourly statistics
        """
        try:
            # Query hourly distribution for last 30 days
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            
            hourly_data = db.session.query(
                extract('hour', MpesaTransaction.created_at).label('hour'),
                func.count(MpesaTransaction.id).label('count'),
                func.sum(
                    case((MpesaTransaction.status == 'success', 1), else_=0)
                ).label('successful')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date
            ).group_by(
                extract('hour', MpesaTransaction.created_at)
            ).order_by(
                extract('hour', MpesaTransaction.created_at)
            ).all()
            
            # Format results
            distribution = []
            for row in hourly_data:
                distribution.append({
                    'hour': int(row.hour),
                    'count': row.count,
                    'successful': row.successful
                })
            
            return distribution
            
        except Exception as e:
            logger.error(f"Error getting hourly distribution: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_top_paying_students(limit=10, days=30):
        """
        Get students with the most M-PESA payments.
        
        Args:
            limit: Number of students to return
            days: Period to analyze
            
        Returns:
            List of top paying students
        """
        try:
            from ..models.academic import Student
            
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            top_students = db.session.query(
                Student.id,
                Student.name,
                Student.admission_number,
                func.count(MpesaTransaction.id).label('payment_count'),
                func.sum(MpesaTransaction.amount).label('total_amount')
            ).join(
                MpesaTransaction, Student.id == MpesaTransaction.student_id
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'success'
            ).group_by(
                Student.id, Student.name, Student.admission_number
            ).order_by(
                func.sum(MpesaTransaction.amount).desc()
            ).limit(limit).all()
            
            # Format results
            students = []
            for row in top_students:
                students.append({
                    'student_id': row.id,
                    'name': row.name,
                    'admission_number': row.admission_number,
                    'payment_count': row.payment_count,
                    'total_amount': float(row.total_amount or 0)
                })
            
            return students
            
        except Exception as e:
            logger.error(f"Error getting top paying students: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_recent_transactions(limit=10):
        """
        Get recent M-PESA transactions.
        
        Args:
            limit: Number of transactions to return
            
        Returns:
            List of recent transactions
        """
        try:
            from ..models.academic import Student
            
            recent = db.session.query(
                MpesaTransaction,
                Student.name.label('student_name'),
                Student.admission_number
            ).outerjoin(
                Student, MpesaTransaction.student_id == Student.id
            ).order_by(
                MpesaTransaction.created_at.desc()
            ).limit(limit).all()
            
            # Format results
            transactions = []
            for tx, student_name, admission_number in recent:
                transactions.append({
                    'id': tx.id,
                    'amount': float(tx.amount),
                    'phone_number': tx.phone_number,
                    'status': tx.status,
                    'created_at': tx.created_at.isoformat() if tx.created_at else None,
                    'mpesa_receipt_number': tx.mpesa_receipt_number,
                    'student_name': student_name,
                    'admission_number': admission_number
                })
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting recent transactions: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_failure_analysis(days=30):
        """
        Analyze failed transactions to identify patterns.
        
        Args:
            days: Period to analyze
            
        Returns:
            Dictionary with failure analysis
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Get failed transactions grouped by result description
            failed_reasons = db.session.query(
                MpesaTransaction.result_desc,
                func.count(MpesaTransaction.id).label('count')
            ).filter(
                MpesaTransaction.created_at >= cutoff_date,
                MpesaTransaction.status == 'failed',
                MpesaTransaction.result_desc.isnot(None)
            ).group_by(
                MpesaTransaction.result_desc
            ).order_by(
                func.count(MpesaTransaction.id).desc()
            ).all()
            
            # Format results
            reasons = []
            for row in failed_reasons:
                reasons.append({
                    'reason': row.result_desc,
                    'count': row.count
                })
            
            return {
                'total_failed': sum(r['count'] for r in reasons),
                'failure_reasons': reasons
            }
            
        except Exception as e:
            logger.error(f"Error analyzing failures: {e}", exc_info=True)
            return {
                'total_failed': 0,
                'failure_reasons': []
            }
