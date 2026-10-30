import Foundation
import AVFoundation

func require(_ success:Bool,_ message:String) {
    if !success { fputs("FAIL: \(message)\n",stderr); exit(1) }
}
func render(_ shift:Int,_ rate:Double=48000,modulated:Bool=false) throws -> ([Float],[Float]) {
    let dsp=try KaraokeDSP(sampleRate:rate); dsp.setPitch(shift)
    let block=512, count=Int(rate*7)/512*512
    var left=[Float](repeating:0,count:block),right=left
    var outputLeft=[Float](),outputRight=[Float]()
    outputLeft.reserveCapacity(count); outputRight.reserveCapacity(count)
    for offset in stride(from:0,to:count,by:block) {
        for i in 0..<block {
            let time=Double(offset+i)/rate
            let amplitude:Double=modulated ? (time.truncatingRemainder(dividingBy:0.5)<0.16 ? 0.3 : 0.015) : 0.3
            left[i]=Float(amplitude*sin(2*Double.pi*440*time)); right[i]=Float(amplitude*sin(2*Double.pi*660*time))
        }
        left.withUnsafeBufferPointer { l in right.withUnsafeBufferPointer { r in dsp.push(l.baseAddress!,r.baseAddress!,frames:block,stride:1) } }
        require(dsp.render(block) == .success,"render status for \(shift)")
        require(dsp.output.frameLength==block,"duration changed")
        let data=dsp.output.floatChannelData!
        for i in 0..<block {
            require(data[0][i].isFinite && data[1][i].isFinite,"non-finite samples")
            outputLeft.append(data[0][i]); outputRight.append(data[1][i])
        }
    }
    require(dsp.overflowFrames==0,"FIFO overflow")
    require(dsp.underrunFrames<Int(rate*0.25),"excessive input starvation: \(dsp.underrunFrames)")
    return (outputLeft,outputRight)
}
func frequency(_ samples:[Float],_ rate:Double) -> Double {
    let start=Int(rate)
    var crossings=0
    for i in start+1..<samples.count { if samples[i-1]<=0 && samples[i]>0 { crossings+=1 } }
    return Double(crossings)*rate/Double(samples.count-start)
}
do {
    for key in [-12,-7,-5,-1,0,1,5,7,12] {
        let (left,right)=try render(key)
        let l=frequency(left,48000),r=frequency(right,48000),ratio=pow(2,Double(key)/12)
        require(abs(l-440*ratio)<2,"left frequency at \(key): \(l)")
        require(abs(r-660*ratio)<3,"right frequency at \(key): \(r)")
        print(String(format:"PASS Apple pitch unit %+d semitones: %.2f/%.2f Hz, fixed duration",key,l,r))
    }
    for rate in [44100.0,96000.0] {
        let (left,_)=try render(-5,rate)
        require(abs(frequency(left,rate)-440*pow(2,-5.0/12))<2,"sample rate \(rate)")
        print("PASS sample rate \(Int(rate))")
    }
    for key in [-12,-5,5,12] {
        let (left,_)=try render(key,modulated:true)
        var peaks=[Double](),wasHigh=false
        for offset in stride(from:48000,to:left.count-480,by:480) {
            var energy:Double=0
            for i in 0..<480 { energy+=Double(left[offset+i]*left[offset+i]) }
            let high=sqrt(energy/480)>0.1,time=Double(offset)/48000
            if high && !wasHigh && (peaks.isEmpty || time-peaks.last!>0.3) { peaks.append(time) }
            wasHigh=high
        }
        require(peaks.count>=10 && peaks.count<=13,"pulse count \(key): \(peaks.count)")
        let interval=(peaks.last!-peaks.first!)/Double(peaks.count-1)
        require(abs(interval-0.5)<0.012,"tempo drift \(key): \(interval)")
        print(String(format:"PASS tempo %+d: %.2fms pulse interval",key,interval*1000))
    }
    print("All native karaoke DSP checks passed")
} catch { fputs("FAIL: \(error)\n",stderr); exit(1) }
