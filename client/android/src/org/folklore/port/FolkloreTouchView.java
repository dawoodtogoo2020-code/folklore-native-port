package org.folklore.port;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;

final class FolkloreTouchView extends View {
    private final FolkloreTouchState state;
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    FolkloreTouchView(Context context,FolkloreTouchState.Sink sink) {
        super(context);state=new FolkloreTouchState(sink);
        setContentDescription("Touch game controls: two sticks, directional pad, face and shoulder buttons");
        setFocusable(false);
    }
    void neutralize() {state.forceNeutral();invalidate();}
    @Override protected void onSizeChanged(int w,int h,int oldw,int oldh) {
        super.onSizeChanged(w,h,oldw,oldh);state.setSize(w,h);
    }
    @Override protected void onDetachedFromWindow() {state.cancelAll();super.onDetachedFromWindow();}
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        for(FolkloreTouchState.Control c:state.controls()) {
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(state.pressed(c)?Color.argb(115,130,185,225):Color.argb(55,25,25,25));
            canvas.drawCircle(c.x,c.y,c.radius,paint);
            paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(Math.max(1,c.radius/24));
            paint.setColor(Color.argb(175,235,235,235));canvas.drawCircle(c.x,c.y,c.radius,paint);
            paint.setStyle(Paint.Style.FILL);
            if(c.kind==FolkloreTouchState.STICK) {
                canvas.drawCircle(c.x+state.axis(c.index)*c.radius*.62f,c.y+state.axis(c.index+1)*c.radius*.62f,c.radius*.31f,paint);
            } else {
                paint.setTextAlign(Paint.Align.CENTER);paint.setTextSize(c.radius*(c.label.length()>4?.42f:.55f));
                canvas.drawText(c.label,c.x,c.y-(paint.ascent()+paint.descent())/2,paint);
            }
        }
    }
    @Override public boolean onTouchEvent(MotionEvent event) {
        if(!isEnabled()||getVisibility()!=VISIBLE)return false;
        int action=event.getActionMasked(),index=event.getActionIndex();
        if(action==MotionEvent.ACTION_CANCEL)state.cancelAll();
        else {
            int count=event.getPointerCount();int[] present=new int[count];
            for(int i=0;i<count;i++)present[i]=event.getPointerId(i);
            state.retainPointers(present);
            if(action==MotionEvent.ACTION_DOWN||action==MotionEvent.ACTION_POINTER_DOWN)
                state.pointerDown(event.getPointerId(index),event.getX(index),event.getY(index));
            for(int i=0;i<count;i++)state.pointerMove(event.getPointerId(i),event.getX(i),event.getY(i));
            if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_POINTER_UP)
                state.pointerUp(event.getPointerId(index));
            if(action==MotionEvent.ACTION_UP) {state.cancelAll();performClick();}
        }
        invalidate();return true;
    }
    @Override public boolean performClick() {super.performClick();return true;}
}
